import hashlib
import html
import json
import re
from typing import Optional

import anthropic

from .cache import Cache
from .models import NormalizedRecord

# Maps common column name variants → standard field
COLUMN_MAP: dict[str, list[str]] = {
    "title":       ["title", "titel", "name", "subject"],
    "description": ["description", "beschreibung", "desc", "text", "caption", "legende"],
    "country":     ["country", "land", "pays", "pais", "country_name"],
    "city":        ["city", "stadt", "ville", "adresse", "address", "ort", "place"],
}

META_SYSTEM_PROMPT = """\
Extract metadata from historical photo archive entries.
For each numbered entry return one JSON object in an array:
{
  "periode": "yyyy" or "yyyy-yyyy" or null,
  "urheber": "photographer/artist name" or null,
  "technik": "photography technique (e.g. Albumin, Photochrome, Tirage vintage, Cyanotype, …)" or null
}
Only extract what is EXPLICITLY stated in the text. Never guess or infer. Return null for any field not clearly present.
Return ONLY the JSON array, no prose.\
"""

SYSTEM_PROMPT = """\
You extract structured location metadata for a historical photo archive.

For each numbered entry, return one JSON object in an array:
{
  "title":         "<max 60 chars, human-readable, same language as the input>",
  "country":       "<English country name, or empty string>",
  "region":        "<canton, state, province or county — e.g. 'Graubünden', 'Lombardia', 'Bavaria' — infer from geographic context clues even when not explicitly named: a well-known mountain range, valley, lake or peak can strongly imply a region (e.g. 'Berninagruppe', 'Corvatsch', 'Piz Roseg', 'Engadin' → 'Graubünden'; 'Vierwaldstättersee', 'Rigi' → 'Luzern'; 'Jungfrau', 'Eiger' → 'Bern'; 'Mont Blanc' → 'Aosta Valley') — empty string only if genuinely unclear>",
  "city":          "<city or town name including disambiguation suffix if present, e.g. 'Bingen am Rhein' not 'Bingen', or empty string>",
  "location":      "<most specific named place – e.g. 'Schloss Vaduz', 'Montalin Schulhaus', 'Viamala-Schlucht' – empty string if only a city, portrait, or generic scene>",
  "location_type": "<the generic building/place word as it appears in the input text, when location is empty but a recognizable generic feature is present — e.g. 'Bahnhof', 'Kirche', 'Schulhaus', 'église', 'stazione' — null otherwise>"
}

Rules for `location`:
- Never translate. Use the original language.
- Split compound nouns when the prefix is a proper noun: 'Montalinschulhaus' → 'Montalin Schulhaus', 'Quaderschulhaus' → 'Quader Schulhaus'.
- If the title itself is a specific named place (especially a compound noun with a proper-noun prefix like 'Montalin-', 'Quader-', 'Kirch-', 'Schloss-'), use it as the location.
- Generic words alone (Kirche, Bahnhof, Schulhaus, Hotel) without a proper-noun modifier → empty string.

Rules for `location_type`:
- Only set when `location` is empty and a generic building type is clearly present.
- Use the word exactly as it appears in the input: 'Bahnhof' not 'railway station', 'Kirche' not 'church'.
- Examples: 'Göschenen, Bahnhof, Gotthardbahn' → location='', location_type='Bahnhof'
- If `location` is non-empty, set location_type to null.

HTML entities are already decoded. Return ONLY the JSON array, no prose.\
"""


def _map_columns(row: dict, extra_desc_cols: list[str] | None = None) -> dict[str, str]:
    low = {str(k).lower().strip(): v for k, v in row.items()}
    out = {f: "" for f in COLUMN_MAP}
    for field, aliases in COLUMN_MAP.items():
        for alias in aliases:
            if alias in low:
                v = low[alias]
                out[field] = "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()
                break
    # Append extra columns to description so Phase 1a Haiku sees them
    if extra_desc_cols:
        extras = []
        for col in extra_desc_cols:
            v = low.get(col.lower().strip())
            v_str = "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()
            if v_str:
                extras.append(v_str)
        if extras:
            sep = " | " if out["description"] else ""
            out["description"] = out["description"] + sep + " | ".join(extras)
    return out


def _clean_wikimedia(text: str) -> str:
    """Strip Wikimedia multilingual markup: 'de|1=German text|1=English text' → 'German text English text'."""
    if '|' not in text or '=' not in text:
        return text
    parts = text.split('|')
    texts = [p.split('=', 1)[1].strip() for p in parts if '=' in p and p.split('=', 1)[1].strip()]
    return ' '.join(texts) if texts else text


def _clean(text: str) -> str:
    return _clean_wikimedia(html.unescape(text or "").strip())


def _raw_key(mapped: dict[str, str]) -> str:
    parts = "|".join(f"{k}:{mapped[k]}" for k in sorted(mapped) if mapped[k])
    return hashlib.sha256(parts.encode()).hexdigest()[:20]


# Swiss/Austrian/German canton and state codes to strip from city names
_REGION_CODES = {
    "AG","AI","AR","BE","BL","BS","FR","GE","GL","GR","JU","LU",
    "NE","NW","OW","SG","SH","SO","SZ","TG","TI","UR","VD","VS",
    "ZG","ZH","LI",  # CH + LI
    "NW","OW",       # NW/OW duplicates — explicit
}


def _normalize_city(city: str) -> str:
    """Strip postal codes, canton codes and similar clutter from city strings.

    Examples: 'Chur GR' → 'Chur', '7000 Chur' → 'Chur', 'Kilchberg (ZH)' → 'Kilchberg'
    """
    if not city:
        return city
    city = city.strip()
    # Remove leading postal code: "7000 Chur" or "CH-7000 Chur"
    city = re.sub(r"^(?:[A-Z]{1,3}-?)?\d{4,5}\s+", "", city)
    # Remove trailing "(ZH)" style
    city = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", city)
    # Remove trailing canton/state code: "Chur GR"
    parts = city.split()
    if len(parts) >= 2 and parts[-1].upper() in _REGION_CODES:
        city = " ".join(parts[:-1])
    return city.strip()


def _meta_raw_key(row: dict) -> str:
    """Cache key for metadata extraction: hash of all non-null raw text values."""
    raw = " ".join(
        html.unescape(str(v)).strip()
        for v in row.values()
        if v is not None and str(v).strip()
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


class InputNormalizer:
    BATCH_SIZE = 20

    def __init__(self, cache: Cache, debug: bool = False, tgn_db=None, geo_db=None,
                 extra_desc_cols: list[str] | None = None):
        self.cache = cache
        self.debug = debug
        self.tgn_db = tgn_db
        self.geo_db = geo_db
        self.extra_desc_cols = extra_desc_cols or []  # extra cols merged into description
        self.haiku_count = 0
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def normalize_batch(self, rows: list[dict]) -> list[NormalizedRecord]:
        total = len(rows)
        results: list[tuple[int, NormalizedRecord]] = []
        pending: list[tuple[int, str, dict[str, str]]] = []

        for i, row in enumerate(rows):
            mapped = {k: _clean(v) for k, v in _map_columns(row, self.extra_desc_cols).items()}
            rk = _raw_key(mapped)
            cached = self.cache.get_norm(rk)
            if cached:
                results.append((i, cached))
            else:
                pending.append((i, rk, mapped))

        if not self.debug and pending:
            print(f"\r  Phase 1: {total - len(pending)}/{total} (cache hits)", end="", flush=True)

        for start in range(0, len(pending), self.BATCH_SIZE):
            batch = pending[start : start + self.BATCH_SIZE]
            ai_records = self._call_ai(batch)
            for (i, rk, _), rec in zip(batch, ai_records):
                self.cache.set_norm(rk, rec)
                results.append((i, rec))
            if not self.debug:
                done = total - len(pending) + start + len(batch)
                print(f"\r  Phase 1: {done}/{total}", end="", flush=True)

        if not self.debug:
            print(f"\r  Phase 1: {total}/{total} done          ")

        results.sort(key=lambda x: x[0])
        normalized = [r for _, r in results]

        # ── Phase 1c: TGN Resolver ───────────────────────────────────────────
        # Fast local lookup — no AI call, no network.
        # Adds tgn_id + tgn_name to each record for use in Phase 2 Step 2.5.
        if self.tgn_db:
            if not self.debug:
                print(f"\r  Phase 1c: TGN lookup…", end="", flush=True)
            hits = 0
            for rec in normalized:
                if rec.location:
                    country_code = (
                        self.geo_db.country_to_code(rec.country)
                        if self.geo_db and rec.country else None
                    )
                    row = self.tgn_db.find(rec.location, country_code=country_code)
                    if row and row["lat"] is not None:
                        rec.tgn_id   = row["tgn_id"]
                        rec.tgn_name = row["pref_name"] or rec.location
                        hits += 1
            if not self.debug:
                print(f"\r  Phase 1c: {hits}/{total} TGN hits          ")

        return normalized

    def extract_metadata_batch(self, rows: list[dict]) -> list[dict]:
        """Extract periode/urheber/technik for each row. Per-row cached by input-text hash."""
        results: list[tuple[int, dict]] = []
        pending: list[tuple[int, str, dict]] = []

        for i, row in enumerate(rows):
            rk = _meta_raw_key(row)
            cached = self.cache.get_meta(rk)
            if cached is not None:
                results.append((i, cached))
            else:
                pending.append((i, rk, row))

        total_meta = len(rows)
        if not self.debug and pending:
            print(f"\r  Phase 1b: {total_meta - len(pending)}/{total_meta} (cache hits)", end="", flush=True)

        for start in range(0, len(pending), self.BATCH_SIZE):
            batch = pending[start : start + self.BATCH_SIZE]
            meta_results = self._call_meta_ai(batch)
            for (i, rk, _), meta in zip(batch, meta_results):
                self.cache.set_meta(rk, meta)
                results.append((i, meta))
            if not self.debug:
                done = total_meta - len(pending) + start + len(batch)
                print(f"\r  Phase 1b: {done}/{total_meta}", end="", flush=True)

        if not self.debug:
            print(f"\r  Phase 1b: {total_meta}/{total_meta} done          ")

        results.sort(key=lambda x: x[0])
        return [r for _, r in results]

    def _call_meta_ai(self, batch: list[tuple]) -> list[dict]:
        lines = []
        for idx, (_, _, row) in enumerate(batch):
            text = " | ".join(
                html.unescape(str(v)).strip()
                for v in row.values()
                if v is not None and str(v).strip()
            )[:400]
            lines.append(f'{idx + 1}. "{text}"')

        self.haiku_count += 1
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max(len(batch) * 60, 256),
            system=META_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Entries:\n" + "\n".join(lines)}],
        )

        raw = response.content[0].text.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            data = json.loads(m.group()) if m else [{}] * len(batch)

        return [
            {
                "periode": item.get("periode"),
                "urheber": item.get("urheber"),
                "technik": item.get("technik"),
            }
            for item in data
        ]

    def _call_ai(self, batch: list[tuple[int, str, dict[str, str]]]) -> list[NormalizedRecord]:
        lines = []
        for idx, (_, _, mapped) in enumerate(batch):
            entry = [f"{idx + 1}."]
            for field in ("title", "description", "country", "city"):
                if mapped[field]:
                    entry.append(f'  {field}="{mapped[field][:300]}"')
            lines.append("\n".join(entry))

        prompt = "Entries:\n" + "\n".join(lines)

        self.haiku_count += 1
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max(len(batch) * 220, 512),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            data = json.loads(m.group()) if m else [{}] * len(batch)

        records = []
        for item, (_, _, mapped) in zip(data, batch):
            ai_city    = str(item.get("city", "")).strip()
            input_city = str(mapped.get("city", "")).strip()
            # If AI city has no substring relation to the input value, the AI likely
            # corrupted it (e.g. "Luzern" → "Lzern"). Fall back to the original.
            if (input_city and ai_city
                    and input_city.lower() not in ai_city.lower()
                    and ai_city.lower() not in input_city.lower()):
                city = _normalize_city(input_city)
            else:
                city = _normalize_city(ai_city if ai_city else input_city)
            location = str(item.get("location", ""))
            # If Haiku found no specific location but identified a generic building
            # type, construct a search string: "Railway Station Göschenen"
            if not location and city:
                loc_type = (item.get("location_type") or "").strip()
                if loc_type:
                    location = f"{loc_type.title()} {city}"
            records.append(
                NormalizedRecord(
                    title=str(item.get("title", mapped.get("title", "")))[:60],
                    description=mapped.get("description", ""),
                    country=str(item.get("country", mapped.get("country", ""))),
                    region=str(item.get("region", "")),
                    city=city,
                    location=location,
                )
            )
        return records

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NormalizedRecord:
    title: str
    description: str
    country: str
    city: str
    location: str = ""     # specific named feature for GeoStack (e.g. "Schloss Vaduz")
    region: str = ""       # canton/state/province hint for city disambiguation
    tgn_id: str = ""       # Getty TGN identifier, set by Phase 1c
    tgn_name: str = ""     # TGN canonical name
    geocoding_queries: list = field(default_factory=list)  # Nominatim queries in local language


@dataclass
class GeoResult:
    lat: Optional[float] = None
    lon: Optional[float] = None
    quality_score: int = 0  # 0=none, 2=nominatim city, 3=geo_db city, 4=nominatim precise, 5=geo_db/tgn precise
    fallback: bool = False   # True = using city center, not the specific location
    source: str = "none"     # "geo_db", "tgn", "country_db", "nominatim", "none"
    match_name: str = ""
    ambiguous: bool = False  # city anchor matched multiple geographically distant candidates
    debug_info: list = field(default_factory=list)  # populated at runtime, not cached

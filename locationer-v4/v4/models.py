from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NormalizedRecord:
    title: str
    description: str
    country: str
    city: str
    location: str = ""    # specific named feature for GeoStack (e.g. "Schloss Vaduz")
    region: str = ""      # canton/state/province hint for city disambiguation (e.g. "Graubünden")
    tgn_id: str = ""          # Getty TGN identifier, set by Phase 1c (e.g. "7003521")
    tgn_name: str = ""        # TGN canonical name (e.g. "Rome")
    geocoding_name: str = ""  # official local-language name for geocoding fallback (Phase 1a)


@dataclass
class GeoResult:
    lat: Optional[float] = None
    lon: Optional[float] = None
    quality_score: int = 0  # 0=none, 2=google city, 3=geo_db city, 4=google precise, 5=geo_db precise
    fallback: bool = False   # True = using city center, not the specific location
    source: str = "none"     # "geo_db", "google", "cache", "none"
    match_name: str = ""
    debug_info: list = field(default_factory=list)  # populated at runtime, not cached

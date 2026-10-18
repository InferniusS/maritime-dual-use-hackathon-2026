"""Versioned user-supplied challenge requirements; no certification is implied."""
from functools import lru_cache
import json
from pathlib import Path

PROFILE_ID = "fincantieri-track2-user-transcription-v1"


@lru_cache(maxsize=1)
def standards_profile():
    return json.loads((Path(__file__).resolve().parents[1] / "standards" / f"{PROFILE_ID}.json").read_text())


def requirement(identifier):
    """Return the central record including authority and interpretation status."""
    return next(item for item in standards_profile()["requirements"] if item["id"] == identifier)


def bend_requirement(nominal_diameter_mm):
    """No interpolation across the explicitly uncovered diameter ranges."""
    if nominal_diameter_mm is None:
        return None
    diameter = float(nominal_diameter_mm)
    for item in requirement("fittings.bend_geometry")["ranges"]:
        if item.get("exact_mm") == diameter or ("max_mm" in item and 0 < diameter <= item["max_mm"]) or ("min_mm" in item and diameter >= item["min_mm"]):
            return dict(item)
    return None

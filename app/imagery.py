"""Recent Sentinel-2 composite.

Esri's World Imagery can be several years old in rural India, and a developer
screening a site needs to know what is there now, not in 2021. A date-filtered
median composite built server-side answers that, and the date range becomes a
visible, citable parameter rather than an unknown.
"""

from __future__ import annotations

from datetime import date, timedelta

SENTINEL2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CLOUD_PROPERTY = "CLOUDY_PIXEL_PERCENTAGE"

# Above this cloud fraction a scene contributes more haze than signal.
MAX_CLOUD_PCT = 35

# True-colour stretch. Sentinel-2 surface reflectance is scaled by 10,000,
# so 0-3000 covers typical land brightness without blowing out bare soil.
VIS = {"bands": ["B4", "B3", "B2"], "min": 0, "max": 3000}


def window_for(months_back: int, today: date | None = None) -> tuple[str, str]:
    """Start and end dates for a lookback window, as ISO strings."""
    today = today or date.today()
    start = today - timedelta(days=int(months_back * 30.44))
    return start.isoformat(), today.isoformat()


def composite_tile_url(aoi, months_back: int, ee) -> tuple[str, dict]:
    """Build a cloud-filtered median composite and return its tile URL.

    `ee` is passed in rather than imported so this module's date logic stays
    testable without the Earth Engine package installed.

    Returns (tile_url, metadata) where metadata records the window and scene
    count, so the imagery date range can be stated alongside the map.
    """
    start, end = window_for(months_back)

    collection = (
        ee.ImageCollection(SENTINEL2_COLLECTION)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lt(CLOUD_PROPERTY, MAX_CLOUD_PCT))
    )

    composite = collection.median().clip(aoi)
    mapid = composite.getMapId(VIS)

    return mapid["tile_fetcher"].url_format, {
        "start": start,
        "end": end,
        "months_back": months_back,
        "max_cloud_pct": MAX_CLOUD_PCT,
        "collection": SENTINEL2_COLLECTION,
    }

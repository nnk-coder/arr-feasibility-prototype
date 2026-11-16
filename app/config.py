"""Configuration, dataset pins, and India-specific presets.

Everything that affects a result and could reasonably be argued about lives
here, so it can be surfaced in the output rather than buried in the logic.
"""

import os

# python-dotenv is a local-development convenience only. Keeping it optional
# means config.py imports with nothing installed, which is what lets the pure
# logic tests run on a fresh checkout.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# Bumped whenever the interface between streamlit_app.py and app/ changes.
# Surfaced in the diagnostics panel so a partial deployment is visible at a
# glance rather than diagnosed from an ImportError.
APP_VERSION = "1.0-full"

# ---------------------------------------------------------------- Earth Engine

GEE_PROJECT = os.getenv("GEE_PROJECT", "")
GEE_CREDENTIALS_JSON = os.getenv("GEE_CREDENTIALS_JSON", "")

# Dataset versions are PINNED, not tracked to latest. Results shift between
# Hansen releases; a feasibility figure that silently changes when UMD
# publishes is a reproducibility problem. Bump deliberately, and the version
# string is printed on every result.
HANSEN_ASSET = "UMD/hansen/global_forest_change_2023_v1_11"
HANSEN_BASE_YEAR = 2000       # treecover band epoch
HANSEN_LAST_LOSS_YEAR = 2023  # lossyear band encodes 1..23 for 2001..2023

WORLDCOVER_COLLECTION = "ESA/WorldCover/v200"   # 10 m, 2021 epoch
WORLDCOVER_EPOCH = 2021
SRTM_ASSET = "USGS/SRTMGL1_003"                 # 30 m elevation, for slope
WDPA_COLLECTION = "WCMC/WDPA/current/polygons"  # protected areas

# ESA WorldCover class codes
WC_TREE_COVER = 10
WC_SHRUBLAND = 20
WC_GRASSLAND = 30
WC_CROPLAND = 40
WC_BUILT_UP = 50
WC_BARE_SPARSE = 60
WC_SNOW_ICE = 70
WC_PERMANENT_WATER = 80
WC_HERBACEOUS_WETLAND = 90
WC_MANGROVES = 95
WC_MOSS_LICHEN = 100

WORLDCOVER_LABELS = {
    WC_TREE_COVER: "Tree cover",
    WC_SHRUBLAND: "Shrubland",
    WC_GRASSLAND: "Grassland",
    WC_CROPLAND: "Cropland",
    WC_BUILT_UP: "Built-up",
    WC_BARE_SPARSE: "Bare / sparse vegetation",
    WC_SNOW_ICE: "Snow and ice",
    WC_PERMANENT_WATER: "Permanent water",
    WC_HERBACEOUS_WETLAND: "Herbaceous wetland",
    WC_MANGROVES: "Mangroves",
    WC_MOSS_LICHEN: "Moss and lichen",
}

# ------------------------------------------------------- Forest definitions

# VM0047 applies the HOST COUNTRY forest definition. For India there are two
# in circulation and they give materially different answers, so both ship and
# the applied one is printed on every result.
#
# A LOWER canopy threshold classifies more land as forest, which makes LESS
# land eligible for ARR. The 10% default is therefore the conservative choice.

FOREST_DEFINITIONS = {
    "India — FSI / UNFCCC (default)": {
        "canopy_pct": 10,
        "min_area_ha": 1.0,
        "min_height_m": 2,
        "source": (
            "Forest Survey of India / India State of Forest Report; the "
            "definition India reports under to UNFCCC and FAO."
        ),
        "note": (
            "Land over 1 ha with tree canopy density above 10%, irrespective "
            "of ownership or legal status. Includes orchards, bamboo and palm."
        ),
    },
    "India — CDM A/R (stricter canopy)": {
        "canopy_pct": 15,
        "min_area_ha": 0.05,
        "min_height_m": 2,
        "source": "Values commonly cited for India's CDM A/R DNA submission.",
        "note": (
            "VERIFY against the current DNA submission before relying on this "
            "for a real project. Shipped as an alternative because A/R project "
            "documentation in India has historically used a 15% crown cover "
            "threshold rather than the 10% national reporting figure."
        ),
    },
}

DEFAULT_FOREST_DEFINITION = "India — FSI / UNFCCC (default)"

# Years of required non-forest history before project start (VM0047).
DEFAULT_LOOKBACK_YEARS = 10

# ------------------------------------------------------------ Flagging rules

# Slope above which planting is flagged for review rather than counted.
FLAG_SLOPE_DEGREES = 30

# Loss detected within this many years BEYOND the lookback window is flagged
# rather than passed — a pixel that was forest 11 years ago deserves a look.
FLAG_LOSS_BUFFER_YEARS = 3

# ------------------------------------------------------------- Area handling

MIN_AOI_HA = 1.0        # below this a 10 m raster has too few pixels to speak
MAX_AOI_HA = 500_000.0  # hard ceiling; beyond this, tile the AOI

# Adaptive analysis scale. Reported alongside every result — silently
# degrading resolution while printing a precise hectare figure is how a tool
# loses credibility.
SCALE_THRESHOLDS = [
    (5_000.0, 10),     # <= 5,000 ha   -> 10 m
    (50_000.0, 30),    # <= 50,000 ha  -> 30 m
    (float("inf"), 100),
]


def scale_for_area(area_ha: float) -> int:
    """Return the analysis scale in metres appropriate to an AOI size."""
    for ceiling, scale in SCALE_THRESHOLDS:
        if area_ha <= ceiling:
            return scale
    return 100


# ------------------------------------------------------------------ Map setup

INDIA_CENTER = (20.5937, 78.9629)
INDIA_ZOOM = 5

# Eligibility class codes and their rendering
CLASS_INELIGIBLE = 0
CLASS_ELIGIBLE = 1
CLASS_FLAGGED = 2

CLASS_PALETTE = ["#9E9E9E", "#2C5F2D", "#F5A623"]  # index == class code

# Per-parcel scoring packs the parcel id and class code into one integer so a
# single grouped reduction returns every (parcel, class) area. The class code
# occupies the units digit, so this must exceed the highest class code.
PARCEL_CLASS_MULTIPLIER = 10
CLASS_LABELS = {
    CLASS_INELIGIBLE: "Ineligible",
    CLASS_ELIGIBLE: "Eligible",
    CLASS_FLAGGED: "Flagged for review",
}

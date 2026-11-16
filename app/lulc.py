"""Land use / land cover classification layer.

ESA WorldCover at 10 m, rendered with the official class palette so the map
matches every other published WorldCover map the user will have seen, and
summarised as an area breakdown.

The breakdown is the most reusable output in the tool — it goes straight into
a project concept note or a PDD land-use section — so it is laid out properly
rather than dumped as a raw histogram.
"""

from __future__ import annotations

from app import config

# Official ESA WorldCover v200 palette, keyed by class code.
WORLDCOVER_PALETTE = {
    10: "#006400",   # Tree cover
    20: "#ffbb22",   # Shrubland
    30: "#ffff4c",   # Grassland
    40: "#f096ff",   # Cropland
    50: "#fa0000",   # Built-up
    60: "#b4b4b4",   # Bare / sparse vegetation
    70: "#f0f0f0",   # Snow and ice
    80: "#0064c8",   # Permanent water
    90: "#0096a0",   # Herbaceous wetland
    95: "#00cf75",   # Mangroves
    100: "#fae6a0",  # Moss and lichen
}

# Short notes on what each class means for an ARR project. This is the part a
# developer actually acts on — "40% cropland" matters because it implies
# displacement and therefore leakage risk.
ARR_RELEVANCE = {
    10: "Already forest — ineligible for ARR.",
    20: "Often suitable for ARR; check it is not naturally regenerating forest.",
    30: "Commonly suitable for ARR.",
    40: "Suitable, but displacing cultivation creates leakage risk.",
    50: "Ineligible.",
    60: "Suitable, though low fertility may limit growth rates.",
    70: "Ineligible.",
    80: "Ineligible.",
    90: "Flagged — possible peat or organic soils.",
    95: "Ineligible under ARR; mangrove restoration uses a different methodology.",
    100: "Marginal; very low growth potential.",
}


def palette_list() -> list[str]:
    """Palette ordered by class code, for a remapped single-band image."""
    return [WORLDCOVER_PALETTE[code] for code in sorted(WORLDCOVER_PALETTE)]


def class_codes() -> list[int]:
    return sorted(WORLDCOVER_PALETTE)


def build_layer(aoi, ee):
    """Return the clipped WorldCover image for the area of interest.

    `ee` is passed in rather than imported so this module stays importable
    without the Earth Engine package, keeping its palette and labels testable
    on a bare checkout.
    """
    return (
        ee.ImageCollection(config.WORLDCOVER_COLLECTION)
        .mosaic()
        .clip(aoi)
        .rename("landcover")
    )


def histogram_reducer(image, aoi, scale, ee):
    """Frequency histogram of land cover classes over the area."""
    return image.reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(),
        geometry=aoi,
        scale=scale,
        maxPixels=1e10,
    )


def summarise(histogram: dict, total_ha: float) -> list[dict]:
    """Turn a raw GEE frequency histogram into a usable breakdown.

    Returns rows ordered by area, each carrying the class label, hectares,
    percentage, palette colour and what the class implies for ARR.
    """
    if not histogram:
        return []

    # GEE returns string keys and float pixel counts.
    counts = {}
    for key, value in histogram.items():
        try:
            counts[int(float(key))] = float(value)
        except (TypeError, ValueError):
            continue

    total_px = sum(counts.values())
    if total_px <= 0:
        return []

    rows = []
    for code, count in counts.items():
        fraction = count / total_px
        rows.append(
            {
                "code": code,
                "label": config.WORLDCOVER_LABELS.get(code, f"Class {code}"),
                "hectares": round(total_ha * fraction, 1),
                "percent": round(fraction * 100, 1),
                "colour": WORLDCOVER_PALETTE.get(code, "#888888"),
                "arr_note": ARR_RELEVANCE.get(code, ""),
            }
        )

    return sorted(rows, key=lambda r: -r["hectares"])


def dominant_class(rows: list[dict]) -> dict | None:
    return rows[0] if rows else None

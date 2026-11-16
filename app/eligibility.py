"""VM0047 ARR eligibility engine.

Builds a per-pixel three-class mask — eligible, flagged, ineligible — from
three independent datasets, renders it as map tiles, and measures each class.

The central methodological point: ARR eligibility turns on the land having
been non-forest for a lookback period (typically 10 years) before project
start. That cannot be answered from one dataset. Hansen treecover2000 is a
snapshot from the year 2000, so on its own it misses land that has reforested
naturally since. Current land cover catches that regrowth; Hansen's loss-year
band catches land that was forest until recently. All three are required.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import date

import ee

from app import config
from app.config import PARCEL_CLASS_MULTIPLIER
from app.gee_init import initialize
from app.validation import check_band_name


@dataclass
class EligibilityResult:
    """Everything a run produces. Serialisable for export and reproducibility."""

    total_ha: float
    eligible_ha: float
    flagged_ha: float
    ineligible_ha: float
    reason_ha: dict[str, float]
    test_coverage_pct: dict[str, float | None]
    scale_m: int
    tile_url: str
    assumptions: dict = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    @property
    def eligible_pct(self) -> float:
        return (self.eligible_ha / self.total_ha * 100) if self.total_ha else 0.0

    @property
    def flagged_pct(self) -> float:
        return (self.flagged_ha / self.total_ha * 100) if self.total_ha else 0.0

    @property
    def upside_ha(self) -> float:
        """Eligible plus flagged — the ceiling if everything flagged clears."""
        return self.eligible_ha + self.flagged_ha




def _loss_year_code(year: int) -> int:
    """Hansen encodes loss year as (year - 2000), so 2016 -> 16."""
    return year - config.HANSEN_BASE_YEAR


def build_masks(
    aoi: ee.Geometry,
    canopy_pct: int,
    lookback_years: int,
    project_start_year: int,
) -> dict:
    """Construct the per-pixel test masks. All server-side, nothing fetched."""

    # unmask(0) on every source is load-bearing, not defensive habit.
    #
    # In Earth Engine a masked pixel propagates through .And()/.Or(): the
    # result is masked, not False. A masked result then makes .where() skip
    # the pixel, so it silently keeps the base value — class 0, ineligible.
    # Hansen is masked over water and outside its land footprint, and slope
    # is masked at nodata, so without this a single masked input anywhere
    # collapses the whole classification to "everything ineligible" with no
    # error raised. Unmasking to 0 makes "no data" mean False, which is the
    # correct reading for every test below.
    hansen = ee.Image(config.HANSEN_ASSET).clip(aoi).unmask(0)
    worldcover = (
        ee.ImageCollection(config.WORLDCOVER_COLLECTION)
        .mosaic()
        .clip(aoi)
        .unmask(0)
    )

    tree_cover_2000 = hansen.select("treecover2000")
    loss_year = hansen.select("lossyear")

    window_start = project_start_year - lookback_years
    window_code = _loss_year_code(window_start)
    buffer_code = _loss_year_code(window_start - config.FLAG_LOSS_BUFFER_YEARS)

    # --- Test 1: is it forest NOW? -------------------------------------
    currently_forest = worldcover.eq(config.WC_TREE_COVER)

    # --- Test 2: was it forest during the lookback window? --------------
    # Two independent ways a pixel fails the lookback test.
    was_forest_2000 = tree_cover_2000.gte(canopy_pct)
    lost_in_window = loss_year.gte(window_code)

    # --- Test 3: hard exclusions ----------------------------------------
    built_up = worldcover.eq(config.WC_BUILT_UP)
    permanent_water = worldcover.eq(config.WC_PERMANENT_WATER)
    snow_ice = worldcover.eq(config.WC_SNOW_ICE)
    mangroves = worldcover.eq(config.WC_MANGROVES)

    # --- Flagging conditions --------------------------------------------
    # Not disqualifying, but a developer should go and look.
    wetland = worldcover.eq(config.WC_HERBACEOUS_WETLAND)

    slope = ee.Terrain.slope(ee.Image(config.SRTM_ASSET)).clip(aoi).unmask(0)
    steep = slope.gt(config.FLAG_SLOPE_DEGREES)

    # Loss just outside the window — a pixel that was forest 11 years ago
    # passes the letter of the test but deserves scrutiny.
    loss_near_boundary = loss_year.gte(buffer_code).And(loss_year.lt(window_code))

    protected = _protected_area_mask(aoi)

    # --- Combine ---------------------------------------------------------
    ineligible = (
        currently_forest
        .Or(was_forest_2000)
        .Or(lost_in_window)
        .Or(built_up)
        .Or(permanent_water)
        .Or(snow_ice)
        .Or(mangroves)
    )

    flagged = (
        wetland.Or(steep).Or(loss_near_boundary).Or(protected)
    ).And(ineligible.Not())

    eligible = ineligible.Not().And(flagged.Not())

    # Built by arithmetic rather than chained .where(). The three classes are
    # mutually exclusive (proven exhaustively in
    # tests/test_classification_logic.py), so summing their weighted masks
    # gives exactly one of 0, 1 or 2 per pixel. Unlike .where(), this cannot
    # silently skip a pixel if an input is masked.
    classified = (
        eligible.multiply(config.CLASS_ELIGIBLE)
        .add(flagged.multiply(config.CLASS_FLAGGED))
        .clip(aoi)
        .rename("class")
    )

    return {
        "classified": classified,
        # Each test on its own, before any combination. Reported as a
        # coverage fraction so a test that fires across the whole area — the
        # signature of a threshold or dataset problem — is visible rather
        # than inferred.
        "raw_tests": {
            "currently_forest": currently_forest,
            "was_forest_at_baseline": was_forest_2000,
            "forest_lost_in_lookback": lost_in_window,
            "built_up": built_up,
            "permanent_water": permanent_water,
            "snow_ice": snow_ice,
            "mangroves": mangroves,
            "wetland": wetland,
            "steep_slope": steep,
            "protected_area": protected,
            "loss_near_window": loss_near_boundary,
            "combined_ineligible": ineligible,
            "combined_flagged": flagged,
            "combined_eligible": eligible,
        },
        "reasons": {
            "currently_forest": currently_forest,
            "was_forest_at_baseline": was_forest_2000,
            "forest_lost_in_lookback": lost_in_window,
            "built_up": built_up,
            "permanent_water": permanent_water,
            "mangroves": mangroves,
            "flag_wetland": wetland.And(ineligible.Not()),
            "flag_steep_slope": steep.And(ineligible.Not()),
            "flag_protected_area": protected.And(ineligible.Not()),
            "flag_loss_near_window": loss_near_boundary.And(ineligible.Not()),
        },
        "window_start": window_start,
    }


def _protected_area_mask(aoi: ee.Geometry) -> ee.Image:
    """Protected-area overlap as a raster mask.

    Falls back to an all-zero mask if the WDPA collection is unavailable, so a
    dataset outage degrades the flagging rather than failing the whole run.
    """
    try:
        wdpa = ee.FeatureCollection(config.WDPA_COLLECTION).filterBounds(aoi)
        # paint() onto an empty image leaves unpainted pixels MASKED, so the
        # unmask(0) is required for the same reason as the sources above.
        return (
            ee.Image()
            .byte()
            .paint(featureCollection=wdpa, color=1)
            .unmask(0)
            .clip(aoi)
            .gt(0)
        )
    except Exception:
        return ee.Image(0).clip(aoi).gt(0)


def analyse(
    geojson_geometry: dict,
    area_ha: float,
    canopy_pct: int = 10,
    lookback_years: int = config.DEFAULT_LOOKBACK_YEARS,
    project_start_year: int | None = None,
    forest_definition_name: str = config.DEFAULT_FOREST_DEFINITION,
) -> EligibilityResult:
    """Run the full eligibility analysis for one boundary.

    Every reduction is batched into a single getInfo() round trip — Earth
    Engine charges per call, and the clip-first pattern keeps the computation
    bounded to the AOI.
    """
    initialize()

    project_start_year = project_start_year or date.today().year
    scale = config.scale_for_area(area_ha)

    aoi = ee.Geometry(geojson_geometry)
    masks = build_masks(aoi, canopy_pct, lookback_years, project_start_year)
    classified = masks["classified"]

    # Class areas, via a grouped reducer on pixel area.
    class_areas = (
        ee.Image.pixelArea()
        .addBands(classified)
        .reduceRegion(
            reducer=ee.Reducer.sum().group(groupField=1, groupName="class"),
            geometry=aoi,
            scale=scale,
            maxPixels=1e10,
        )
    )

    # Reason areas: stack every mask as a band and reduce once, rather than
    # one reduceRegion per reason.
    reason_names = list(masks["reasons"].keys())
    reason_stack = ee.Image.cat(
        [masks["reasons"][name].rename(check_band_name(name)) for name in reason_names]
    ).multiply(ee.Image.pixelArea())

    reason_areas = reason_stack.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=scale,
        maxPixels=1e10,
    )

    # Coverage fraction of each individual test, for diagnostics.
    raw_names = list(masks["raw_tests"].keys())
    raw_stack = ee.Image.cat(
        [masks["raw_tests"][name].rename(check_band_name(name)) for name in raw_names]
    )
    raw_coverage = raw_stack.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=aoi,
        scale=scale,
        maxPixels=1e10,
    )

    # ONE round trip for all three.
    payload = ee.Dictionary(
        {
            "classes": class_areas,
            "reasons": reason_areas,
            "coverage": raw_coverage,
        }
    ).getInfo()

    # Tiles for the map overlay. selfMask drops class 0 so the basemap shows
    # through on ineligible land rather than covering it in grey.
    mapid = classified.selfMask().getMapId(
        {
            "min": config.CLASS_INELIGIBLE,
            "max": config.CLASS_FLAGGED,
            "palette": config.CLASS_PALETTE,
        }
    )
    tile_url = mapid["tile_fetcher"].url_format

    by_class = {
        int(g["class"]): g["sum"] / 10_000.0
        for g in payload["classes"].get("groups", [])
    }
    reason_ha = {
        name: (payload["reasons"].get(name) or 0.0) / 10_000.0
        for name in reason_names
    }
    coverage = {
        name: (
            None
            if payload["coverage"].get(name) is None
            else round(payload["coverage"][name] * 100, 2)
        )
        for name in raw_names
    }

    caveats = _build_caveats(project_start_year, masks["window_start"], scale)

    return EligibilityResult(
        total_ha=round(area_ha, 1),
        eligible_ha=round(by_class.get(config.CLASS_ELIGIBLE, 0.0), 1),
        flagged_ha=round(by_class.get(config.CLASS_FLAGGED, 0.0), 1),
        ineligible_ha=round(by_class.get(config.CLASS_INELIGIBLE, 0.0), 1),
        reason_ha={k: round(v, 1) for k, v in reason_ha.items()},
        test_coverage_pct=coverage,
        scale_m=scale,
        tile_url=tile_url,
        assumptions={
            "forest_definition": forest_definition_name,
            "canopy_threshold_pct": canopy_pct,
            "lookback_years": lookback_years,
            "project_start_year": project_start_year,
            "non_forest_required_from": masks["window_start"],
            "analysis_scale_m": scale,
            "hansen_asset": config.HANSEN_ASSET,
            "worldcover_collection": config.WORLDCOVER_COLLECTION,
            "worldcover_epoch": config.WORLDCOVER_EPOCH,
            "slope_flag_threshold_deg": config.FLAG_SLOPE_DEGREES,
        },
        caveats=caveats,
    )


def _build_caveats(project_start_year: int, window_start: int, scale: int) -> list[str]:
    """Data currency and resolution limitations, stated rather than buried."""
    caveats = []

    hansen_gap = project_start_year - config.HANSEN_LAST_LOSS_YEAR
    if hansen_gap > 0:
        caveats.append(
            f"Forest loss data ends {config.HANSEN_LAST_LOSS_YEAR}; the last "
            f"{hansen_gap} year(s) of the lookback window are not covered. "
            "Clearing since then will not appear in this analysis."
        )

    wc_gap = project_start_year - config.WORLDCOVER_EPOCH
    if wc_gap > 0:
        caveats.append(
            f"Land cover is from the {config.WORLDCOVER_EPOCH} epoch, "
            f"{wc_gap} year(s) before the assumed project start. Recent land "
            "use change will not be reflected."
        )

    if scale > 10:
        caveats.append(
            f"Analysis ran at {scale} m rather than 10 m because of the area "
            "size. Small features may be missed and class boundaries are "
            "coarser than the headline figures imply."
        )

    caveats.append(
        "Herbaceous wetland is used as a proxy for peat and organic soils; no "
        "free global peat layer exists at usable resolution. Wetland pixels "
        "are flagged, not excluded."
    )
    caveats.append(
        "All figures derive from global public datasets and are indicative "
        "only. They do not replace a ground survey."
    )
    return caveats




@dataclass
class ParcelResult:
    """Eligibility breakdown for one parcel."""

    name: str
    source: str
    footprint_ha: float      # the parcel's own area
    scored_ha: float         # area actually scored (less, if parcels overlap)
    eligible_ha: float
    flagged_ha: float
    ineligible_ha: float

    @property
    def eligible_pct(self) -> float:
        return (self.eligible_ha / self.scored_ha * 100) if self.scored_ha else 0.0

    @property
    def upside_ha(self) -> float:
        return self.eligible_ha + self.flagged_ha


def analyse_by_parcel(
    parcels: list,
    merged_geojson: dict,
    total_area_ha: float,
    canopy_pct: int = 10,
    lookback_years: int = config.DEFAULT_LOOKBACK_YEARS,
    project_start_year: int | None = None,
    forest_definition_name: str = config.DEFAULT_FOREST_DEFINITION,
) -> tuple[EligibilityResult, list[ParcelResult]]:
    """Score each parcel separately, plus the combined total.

    Scoring N parcels naively costs N round trips. Instead the parcel index is
    painted into a raster and combined with the class code into a single
    value, so ONE grouped reduction returns every (parcel, class) area. The
    cost is the same as the merged analysis regardless of parcel count.

    Overlapping ground belongs to whichever parcel paints last, so parcel
    areas sum to the merged total rather than double counting.
    """
    initialize()

    project_start_year = project_start_year or date.today().year
    scale = config.scale_for_area(total_area_ha)
    aoi = ee.Geometry(merged_geojson)

    masks = build_masks(aoi, canopy_pct, lookback_years, project_start_year)
    classified = masks["classified"]

    # Parcel ids are 1-based so that 0 means "outside every parcel", which
    # can happen at raster edges.
    features = [
        ee.Feature(ee.Geometry(p.geojson), {"pid": idx + 1})
        for idx, p in enumerate(parcels)
    ]
    parcel_fc = ee.FeatureCollection(features)
    parcel_img = (
        ee.Image().int().paint(featureCollection=parcel_fc, color="pid").unmask(0)
    )

    composite = (
        parcel_img.multiply(PARCEL_CLASS_MULTIPLIER)
        .add(classified)
        .rename("pc")
        .clip(aoi)
    )

    grouped = (
        ee.Image.pixelArea()
        .addBands(composite)
        .reduceRegion(
            reducer=ee.Reducer.sum().group(groupField=1, groupName="pc"),
            geometry=aoi,
            scale=scale,
            maxPixels=1e10,
        )
    )

    reason_names = list(masks["reasons"].keys())
    reason_stack = ee.Image.cat(
        [masks["reasons"][n].rename(check_band_name(n)) for n in reason_names]
    ).multiply(ee.Image.pixelArea())
    reason_areas = reason_stack.reduceRegion(
        reducer=ee.Reducer.sum(), geometry=aoi, scale=scale, maxPixels=1e10
    )

    raw_names = list(masks["raw_tests"].keys())
    raw_stack = ee.Image.cat(
        [masks["raw_tests"][n].rename(check_band_name(n)) for n in raw_names]
    )
    raw_coverage = raw_stack.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=aoi, scale=scale, maxPixels=1e10
    )

    payload = ee.Dictionary(
        {
            "grouped": grouped,
            "reasons": reason_areas,
            "coverage": raw_coverage,
        }
    ).getInfo()

    tile_url = classified.selfMask().getMapId(
        {
            "min": config.CLASS_INELIGIBLE,
            "max": config.CLASS_FLAGGED,
            "palette": config.CLASS_PALETTE,
        }
    )["tile_fetcher"].url_format

    # Decode the composite key back into (parcel index, class).
    per_parcel: dict[int, dict[int, float]] = {}
    totals: dict[int, float] = {}
    for group in payload["grouped"].get("groups", []):
        code = int(group["pc"])
        ha = (group["sum"] or 0.0) / 10_000.0
        pid, cls = divmod(code, PARCEL_CLASS_MULTIPLIER)
        if pid == 0:
            continue  # outside every parcel
        per_parcel.setdefault(pid, {})[cls] = ha
        totals[cls] = totals.get(cls, 0.0) + ha

    parcel_results = []
    for idx, parcel in enumerate(parcels, start=1):
        by_class = per_parcel.get(idx, {})
        elig = by_class.get(config.CLASS_ELIGIBLE, 0.0)
        flag = by_class.get(config.CLASS_FLAGGED, 0.0)
        inel = by_class.get(config.CLASS_INELIGIBLE, 0.0)
        parcel_results.append(
            ParcelResult(
                name=parcel.name,
                source=parcel.source,
                footprint_ha=round(parcel.area_ha, 1),
                scored_ha=round(elig + flag + inel, 1),
                eligible_ha=round(elig, 1),
                flagged_ha=round(flag, 1),
                ineligible_ha=round(inel, 1),
            )
        )

    coverage = {
        n: (
            None
            if payload["coverage"].get(n) is None
            else round(payload["coverage"][n] * 100, 2)
        )
        for n in raw_names
    }

    combined = EligibilityResult(
        total_ha=round(total_area_ha, 1),
        eligible_ha=round(totals.get(config.CLASS_ELIGIBLE, 0.0), 1),
        flagged_ha=round(totals.get(config.CLASS_FLAGGED, 0.0), 1),
        ineligible_ha=round(totals.get(config.CLASS_INELIGIBLE, 0.0), 1),
        reason_ha={
            n: round((payload["reasons"].get(n) or 0.0) / 10_000.0, 1)
            for n in reason_names
        },
        test_coverage_pct=coverage,
        scale_m=scale,
        tile_url=tile_url,
        assumptions={
            "forest_definition": forest_definition_name,
            "canopy_threshold_pct": canopy_pct,
            "lookback_years": lookback_years,
            "project_start_year": project_start_year,
            "non_forest_required_from": masks["window_start"],
            "analysis_scale_m": scale,
            "parcel_count": len(parcels),
            "scoring_mode": "per-parcel",
            "hansen_asset": config.HANSEN_ASSET,
            "worldcover_collection": config.WORLDCOVER_COLLECTION,
            "worldcover_epoch": config.WORLDCOVER_EPOCH,
            "slope_flag_threshold_deg": config.FLAG_SLOPE_DEGREES,
        },
        caveats=_build_caveats(project_start_year, masks["window_start"], scale),
    )

    return combined, parcel_results

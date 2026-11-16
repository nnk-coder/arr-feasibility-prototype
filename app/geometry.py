"""Boundary parsing and validation.

Takes an uploaded KML / KMZ / GeoJSON / zipped Shapefile and returns a single
validated WGS84 polygon plus its true area. Everything here runs before any
Earth Engine call, so a bad boundary fails loudly and cheaply instead of
producing a confident wrong answer downstream.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from app.config import MAX_AOI_HA, MIN_AOI_HA

# KML is parsed by the XML reader below rather than through a GDAL driver.
# Fiona's KML support depends on how GDAL was compiled and is missing from
# most slim Linux images, including Streamlit Community Cloud's, so relying on
# it would make KML upload fail for an environment reason. Shapefiles go
# through geopandas, which uses pyogrio and needs no fiona either.


class BoundaryError(ValueError):
    """Raised when an uploaded boundary cannot be used."""


@dataclass
class Parcel:
    """One named polygon. The unit a developer makes a keep-or-drop call on."""

    name: str
    geometry: Polygon
    area_ha: float
    source: str  # originating filename, or "drawn"

    @property
    def geojson(self) -> dict:
        return mapping(self.geometry)


@dataclass
class Boundary:
    """A validated project boundary: one or more named parcels."""

    parcels: list[Parcel]
    geometry: Polygon | MultiPolygon  # the union, for merged analysis
    area_ha: float
    source_format: str
    warnings: list[str] = field(default_factory=list)

    @property
    def geojson(self) -> dict:
        return mapping(self.geometry)

    @property
    def part_count(self) -> int:
        return len(self.parcels)

    @property
    def centroid(self) -> tuple[float, float]:
        c = self.geometry.centroid
        return (c.y, c.x)  # lat, lon — folium's order

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.geometry.bounds  # minx, miny, maxx, maxy

    @property
    def sources(self) -> list[str]:
        seen: list[str] = []
        for p in self.parcels:
            if p.source not in seen:
                seen.append(p.source)
        return seen


# --------------------------------------------------------------- area helpers


def equal_area_hectares(geom) -> float:
    """Area in hectares via a Lambert azimuthal equal-area projection centred
    on the geometry itself.

    Computing area from degrees is wrong by a factor that varies with latitude,
    and a fixed national projection distorts at the edges of a large country.
    Centring the projection on the geometry keeps the error negligible at any
    size this tool accepts.
    """
    from pyproj import CRS, Transformer

    centroid = geom.centroid
    laea = CRS.from_proj4(
        f"+proj=laea +lat_0={centroid.y} +lon_0={centroid.x} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    transformer = Transformer.from_crs("EPSG:4326", laea, always_xy=True)
    projected = shapely_transform(transformer.transform, geom)
    return projected.area / 10_000.0


# ------------------------------------------------------------- KML fallback

_KML_NS = re.compile(r"\{.*?\}")


def _strip_ns(tag: str) -> str:
    return _KML_NS.sub("", tag)


def _parse_kml_coordinates(text: str) -> list[tuple[float, float]]:
    """Parse a KML <coordinates> blob into (lon, lat) pairs."""
    coords = []
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                coords.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return coords


def _polygon_from_kml_element(elem) -> Polygon | None:
    """Build a Polygon from a single KML <Polygon> element."""
    outer: list[tuple[float, float]] = []
    inners: list[list[tuple[float, float]]] = []

    for child in elem.iter():
        tag = _strip_ns(child.tag)
        if tag not in ("outerBoundaryIs", "innerBoundaryIs"):
            continue
        for coord_elem in child.iter():
            if _strip_ns(coord_elem.tag) != "coordinates":
                continue
            ring = _parse_kml_coordinates(coord_elem.text or "")
            if len(ring) < 4:
                continue
            if tag == "outerBoundaryIs" and not outer:
                outer = ring
            elif tag == "innerBoundaryIs":
                inners.append(ring)

    if len(outer) < 4:
        return None
    try:
        return Polygon(outer, inners)
    except Exception:
        return None


def _named_polygons_from_kml_bytes(data: bytes) -> list[tuple[str | None, Polygon]]:
    """Extract (name, polygon) pairs from KML without a GDAL driver.

    Fiona's KML support depends on how GDAL was built and is routinely missing
    on slim Linux images, so this walks the XML directly.

    Names come from each Placemark's <name>. Most KMLs people hold — exported
    from a government portal or a previous survey — carry meaningful parcel
    names there, which are far more useful in a results table than "Parcel 3".
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise BoundaryError(f"File is not valid XML: {exc}") from exc

    results: list[tuple[str | None, Polygon]] = []
    claimed: set[int] = set()

    for placemark in root.iter():
        if _strip_ns(placemark.tag) != "Placemark":
            continue

        name = None
        for child in placemark:
            if _strip_ns(child.tag) == "name":
                name = (child.text or "").strip() or None
                break

        polys_here = [
            e for e in placemark.iter() if _strip_ns(e.tag) == "Polygon"
        ]
        for idx, poly_elem in enumerate(polys_here):
            claimed.add(id(poly_elem))
            poly = _polygon_from_kml_element(poly_elem)
            if poly is None:
                continue
            # A MultiGeometry holds several polygons under one name; number
            # them so they stay distinguishable in the results table.
            if name and len(polys_here) > 1:
                results.append((f"{name} ({idx + 1})", poly))
            else:
                results.append((name, poly))

    # Polygons sitting outside any Placemark. Unusual, but some exporters
    # produce them and dropping them silently would lose project area.
    for elem in root.iter():
        if _strip_ns(elem.tag) != "Polygon" or id(elem) in claimed:
            continue
        poly = _polygon_from_kml_element(elem)
        if poly is not None:
            results.append((None, poly))

    return results


# ----------------------------------------------------------------- loaders


# Property keys checked, in order, for a feature's name.
NAME_KEYS = ("name", "Name", "NAME", "title", "Title", "label", "parcel", "PARCEL")


def _name_from_properties(props: dict | None) -> str | None:
    if not props:
        return None
    for key in NAME_KEYS:
        value = props.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _load_geojson(data: bytes) -> list[tuple[str | None, object]]:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundaryError(f"Not valid GeoJSON: {exc}") from exc

    pairs: list[tuple[str | None, object]] = []
    if obj.get("type") == "FeatureCollection":
        for feat in obj.get("features", []):
            if feat.get("geometry"):
                pairs.append(
                    (_name_from_properties(feat.get("properties")), shape(feat["geometry"]))
                )
    elif obj.get("type") == "Feature":
        pairs.append((_name_from_properties(obj.get("properties")), shape(obj["geometry"])))
    else:
        pairs.append((None, shape(obj)))

    if not pairs:
        raise BoundaryError("GeoJSON contains no geometries.")
    return pairs


def _load_kmz(data: bytes) -> list[tuple[str | None, Polygon]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BoundaryError(f"KMZ is not a readable archive: {exc}") from exc

    kml_names = [n for n in archive.namelist() if n.lower().endswith(".kml")]
    if not kml_names:
        raise BoundaryError("KMZ contains no .kml file.")

    pairs: list[tuple[str | None, Polygon]] = []
    for name in kml_names:
        pairs.extend(_named_polygons_from_kml_bytes(archive.read(name)))
    if not pairs:
        raise BoundaryError("No polygons found in the KMZ.")
    return pairs


def _load_zipped_shapefile(data: bytes) -> list[tuple[str | None, object]]:
    import geopandas as gpd

    try:
        gdf = gpd.read_file(io.BytesIO(data))
    except Exception as exc:
        raise BoundaryError(f"Could not read zipped Shapefile: {exc}") from exc

    if gdf.empty:
        raise BoundaryError("Shapefile contains no features.")
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)

    name_col = next(
        (c for c in NAME_KEYS if c in gdf.columns),
        None,
    )
    pairs = []
    for _, row in gdf.iterrows():
        raw = row[name_col] if name_col else None
        label = str(raw).strip() if raw is not None and str(raw).strip() else None
        pairs.append((label, row.geometry))
    return pairs


# ------------------------------------------------------------ main entrypoint


def _explode_to_polygons(
    pairs: list[tuple[str | None, object]],
) -> tuple[list[tuple[str | None, Polygon]], int]:
    """Flatten geometries to polygons, keeping each one's name."""
    out: list[tuple[str | None, Polygon]] = []
    skipped = 0
    for name, geom in pairs:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, Polygon):
            out.append((name, geom))
        elif isinstance(geom, MultiPolygon):
            parts = list(geom.geoms)
            for idx, part in enumerate(parts):
                label = (
                    f"{name} ({idx + 1})" if name and len(parts) > 1 else name
                )
                out.append((label, part))
        else:
            skipped += 1
    return out, skipped


def _repair(poly: Polygon, warnings: list[str]) -> Polygon:
    """Fix a self-intersecting polygon, reporting rather than hiding it."""
    if poly.is_valid:
        return poly
    fixed = poly.buffer(0)
    if fixed.is_empty or not fixed.is_valid:
        raise BoundaryError(
            "A polygon could not be repaired. Check for self-intersecting "
            "boundaries."
        )
    warnings.append(
        "Repaired a self-intersecting polygon with a zero-width buffer; "
        "verify the boundary still matches your intent."
    )
    return fixed


def parcels_from_file(filename: str, data: bytes) -> list[Parcel]:
    """Parse one uploaded file into named parcels.

    Raises BoundaryError with a message meant for the user.
    """
    lower = filename.lower()

    if lower.endswith((".geojson", ".json")):
        pairs, fmt = _load_geojson(data), "GeoJSON"
    elif lower.endswith(".kml"):
        pairs, fmt = _named_polygons_from_kml_bytes(data), "KML"
    elif lower.endswith(".kmz"):
        pairs, fmt = _load_kmz(data), "KMZ"
    elif lower.endswith(".zip"):
        pairs, fmt = _load_zipped_shapefile(data), "Shapefile"
    else:
        raise BoundaryError(
            "Unsupported file type. Upload a .kml, .kmz, .geojson, or a "
            "zipped Shapefile (.zip)."
        )

    if not pairs:
        raise BoundaryError(f"No polygons found in the {fmt} file.")

    exploded, skipped = _explode_to_polygons(pairs)
    if not exploded:
        raise BoundaryError(
            f"{filename} contained no usable polygon features"
            + (f" ({skipped} non-polygon feature(s) ignored)." if skipped else ".")
        )

    warnings: list[str] = []
    parcels: list[Parcel] = []
    for idx, (name, poly) in enumerate(exploded, start=1):
        poly = _repair(poly, warnings)
        _check_coordinate_range(poly)
        parcels.append(
            Parcel(
                name=name or f"{filename} · {idx}",
                geometry=poly,
                area_ha=equal_area_hectares(poly),
                source=filename,
            )
        )
    return parcels


def parcels_from_drawings(drawings: list[dict], start_index: int = 1) -> list[Parcel]:
    """Convert folium Draw output into parcels. Drawn shapes carry no name."""
    parcels: list[Parcel] = []
    warnings: list[str] = []
    pairs = [(None, shape(d["geometry"])) for d in drawings if d.get("geometry")]
    exploded, _ = _explode_to_polygons(pairs)
    for offset, (_, poly) in enumerate(exploded):
        poly = _repair(poly, warnings)
        _check_coordinate_range(poly)
        parcels.append(
            Parcel(
                name=f"Drawn {start_index + offset}",
                geometry=poly,
                area_ha=equal_area_hectares(poly),
                source="drawn",
            )
        )
    return parcels


def _check_coordinate_range(geom) -> None:
    """Catch a projected file claiming to be WGS84 — common with a missing .prj."""
    minx, miny, maxx, maxy = geom.bounds
    if not (
        -180 <= minx <= 180
        and -180 <= maxx <= 180
        and -90 <= miny <= 90
        and -90 <= maxy <= 90
    ):
        raise BoundaryError(
            "Coordinates fall outside valid latitude/longitude ranges. The "
            "file may be in a projected CRS without a .prj definition — "
            "reproject it to EPSG:4326 and try again."
        )


def build_boundary_from_parcels(parcels: list[Parcel]) -> Boundary:
    """Combine parcels into a Boundary, validating the total.

    Parcel names are made unique here rather than at parse time, because a
    collision can only appear once parcels from several files sit together.
    """
    if not parcels:
        raise BoundaryError("No parcels loaded.")

    warnings: list[str] = []

    # Disambiguate duplicate names into COPIES, never by mutating the inputs.
    #
    # The caller holds these parcels in session state and re-derives them from
    # the same uploads on every rerun, deduplicating on (source, name).
    # Renaming in place would change that key, so the next rerun would no
    # longer recognise the parcel and would append it again — parcels would
    # multiply on every map pan and silently inflate the project area.
    seen: dict[str, int] = {}
    display: list[Parcel] = []
    for parcel in parcels:
        base = parcel.name
        if base in seen:
            seen[base] += 1
            display.append(replace(parcel, name=f"{base} #{seen[base]}"))
        else:
            seen[base] = 1
            display.append(parcel)
    parcels = display

    merged = unary_union([p.geometry for p in parcels])
    if not isinstance(merged, (Polygon, MultiPolygon)):
        raise BoundaryError("Combined boundary is not a polygon.")

    # Overlap matters: a merged total counts shared ground once, but the sum
    # of parcel areas counts it twice. Saying so beats two figures that
    # quietly disagree.
    sum_parts = sum(p.area_ha for p in parcels)
    area_ha = equal_area_hectares(merged)
    if sum_parts - area_ha > max(0.5, area_ha * 0.001):
        warnings.append(
            f"Parcels overlap: they sum to {sum_parts:,.1f} ha but cover "
            f"{area_ha:,.1f} ha of distinct ground. Overlapping ground is "
            "counted once and assigned to the later parcel, so a parcel's "
            "scored area may be smaller than its own footprint."
        )

    if area_ha < MIN_AOI_HA:
        raise BoundaryError(
            f"Total area is {area_ha:.2f} ha, below the {MIN_AOI_HA:g} ha "
            "minimum. A 10 m raster has too few pixels at this size to say "
            "anything meaningful."
        )
    if area_ha > MAX_AOI_HA:
        raise BoundaryError(
            f"Total area is {area_ha:,.0f} ha, above the {MAX_AOI_HA:,.0f} ha "
            "ceiling. Analyse fewer parcels at a time."
        )

    sources = []
    for p in parcels:
        if p.source not in sources:
            sources.append(p.source)
    fmt = sources[0] if len(sources) == 1 else f"{len(sources)} sources"

    return Boundary(
        parcels=parcels,
        geometry=merged,
        area_ha=area_ha,
        source_format=fmt,
        warnings=warnings,
    )

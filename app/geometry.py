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
from dataclasses import dataclass, field

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
class Boundary:
    """A validated project boundary."""

    geometry: Polygon | MultiPolygon
    area_ha: float
    part_count: int
    source_format: str
    warnings: list[str] = field(default_factory=list)

    @property
    def geojson(self) -> dict:
        return mapping(self.geometry)

    @property
    def centroid(self) -> tuple[float, float]:
        c = self.geometry.centroid
        return (c.y, c.x)  # lat, lon — folium's order

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.geometry.bounds  # minx, miny, maxx, maxy


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


def _polygons_from_kml_bytes(data: bytes) -> list[Polygon]:
    """Extract polygons from KML without relying on a GDAL driver.

    Fiona's KML support depends on how GDAL was built, and it is routinely
    missing on slim Linux images. This walks the XML directly so a KML upload
    never fails for an environment reason.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise BoundaryError(f"File is not valid XML: {exc}") from exc

    polygons: list[Polygon] = []
    for elem in root.iter():
        if _strip_ns(elem.tag) != "Polygon":
            continue

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

        if len(outer) >= 4:
            try:
                polygons.append(Polygon(outer, inners))
            except Exception:
                continue

    return polygons


# ----------------------------------------------------------------- loaders


def _load_geojson(data: bytes) -> list:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundaryError(f"Not valid GeoJSON: {exc}") from exc

    if obj.get("type") == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in obj.get("features", []) if f.get("geometry")]
    elif obj.get("type") == "Feature":
        geoms = [shape(obj["geometry"])]
    else:
        geoms = [shape(obj)]

    if not geoms:
        raise BoundaryError("GeoJSON contains no geometries.")
    return geoms


def _load_kmz(data: bytes) -> list:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BoundaryError(f"KMZ is not a readable archive: {exc}") from exc

    kml_names = [n for n in archive.namelist() if n.lower().endswith(".kml")]
    if not kml_names:
        raise BoundaryError("KMZ contains no .kml file.")

    polygons: list[Polygon] = []
    for name in kml_names:
        polygons.extend(_polygons_from_kml_bytes(archive.read(name)))
    if not polygons:
        raise BoundaryError("No polygons found in the KMZ.")
    return polygons


def _load_zipped_shapefile(data: bytes) -> list:
    import geopandas as gpd

    try:
        gdf = gpd.read_file(io.BytesIO(data))
    except Exception as exc:
        raise BoundaryError(f"Could not read zipped Shapefile: {exc}") from exc

    if gdf.empty:
        raise BoundaryError("Shapefile contains no features.")
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    return list(gdf.geometry)


# ------------------------------------------------------------ main entrypoint


def load_boundary(filename: str, data: bytes) -> Boundary:
    """Parse and validate an uploaded boundary file.

    Raises BoundaryError with a message meant for the user, not a stack trace.
    """
    name = filename.lower()
    warnings: list[str] = []

    if name.endswith(".geojson") or name.endswith(".json"):
        geoms, fmt = _load_geojson(data), "GeoJSON"
    elif name.endswith(".kml"):
        geoms, fmt = _polygons_from_kml_bytes(data), "KML"
    elif name.endswith(".kmz"):
        geoms, fmt = _load_kmz(data), "KMZ"
    elif name.endswith(".zip"):
        geoms, fmt = _load_zipped_shapefile(data), "Shapefile (zipped)"
    else:
        raise BoundaryError(
            "Unsupported file type. Upload a .kml, .kmz, .geojson, or a "
            "zipped Shapefile (.zip)."
        )

    if not geoms:
        raise BoundaryError(f"No polygons found in the {fmt} file.")

    return build_boundary(geoms, source_format=fmt, warnings=warnings)


def build_boundary(
    geoms: list,
    source_format: str = "drawn",
    warnings: list[str] | None = None,
) -> Boundary:
    """Validate a list of shapely geometries into a single Boundary."""
    warnings = list(warnings or [])

    polygons: list[Polygon] = []
    skipped_non_polygon = 0
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, Polygon):
            polygons.append(geom)
        elif isinstance(geom, MultiPolygon):
            polygons.extend(list(geom.geoms))
        else:
            skipped_non_polygon += 1

    if skipped_non_polygon:
        warnings.append(
            f"Ignored {skipped_non_polygon} non-polygon feature(s) — only "
            "polygons can be analysed."
        )
    if not polygons:
        raise BoundaryError("File contained no usable polygon features.")

    # Repair self-intersections. A zero-width buffer is the standard fix and
    # is reported rather than applied silently, since it can alter the shape.
    repaired = []
    for poly in polygons:
        if poly.is_valid:
            repaired.append(poly)
            continue
        fixed = poly.buffer(0)
        if fixed.is_empty or not fixed.is_valid:
            raise BoundaryError(
                "A polygon in this file is invalid and could not be repaired. "
                "Check for self-intersecting boundaries."
            )
        repaired.append(fixed)
        warnings.append(
            "Repaired a self-intersecting polygon with a zero-width buffer; "
            "verify the boundary still matches your intent."
        )

    merged = unary_union(repaired)
    if isinstance(merged, Polygon):
        merged = merged
        part_count = 1
    elif isinstance(merged, MultiPolygon):
        part_count = len(merged.geoms)
        warnings.append(
            f"Boundary has {part_count} separate parts; they are analysed "
            "together as one project area."
        )
    else:
        raise BoundaryError("Merged boundary is not a polygon.")

    # Coordinate sanity. Catches a projected file that claims to be WGS84 —
    # a common failure with shapefiles that carry no .prj.
    minx, miny, maxx, maxy = merged.bounds
    if not (-180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90):
        raise BoundaryError(
            "Coordinates fall outside valid latitude/longitude ranges. The "
            "file may be in a projected CRS without a .prj definition — "
            "reproject it to EPSG:4326 and try again."
        )

    area_ha = equal_area_hectares(merged)

    if area_ha < MIN_AOI_HA:
        raise BoundaryError(
            f"Area is {area_ha:.2f} ha, below the {MIN_AOI_HA:g} ha minimum. "
            "A 10 m raster has too few pixels at this size to say anything "
            "meaningful."
        )
    if area_ha > MAX_AOI_HA:
        raise BoundaryError(
            f"Area is {area_ha:,.0f} ha, above the {MAX_AOI_HA:,.0f} ha "
            "ceiling. Split the boundary into tiles and analyse them "
            "separately."
        )

    return Boundary(
        geometry=merged,
        area_ha=area_ha,
        part_count=part_count,
        source_format=source_format,
        warnings=warnings,
    )

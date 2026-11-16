"""Folium map construction.

Basemaps are chosen for zero-cost operation: none of these require an API key
or a billing account. Esri World Imagery is the satellite default; Google
Maps would need a Maps JS key tied to an enabled billing account, which is the
only reason it is not here.
"""

from __future__ import annotations

import folium
from folium.plugins import Draw

from app import config

# Zoom beyond which a layer has no cached tiles.
#
# Requesting a tile past a service's cached levels returns Esri's "no data
# available" image rather than an error, so the map simply goes blank as the
# user zooms in to inspect a parcel. Setting max_native_zoom makes Leaflet
# upscale the deepest real tile instead of asking for one that does not
# exist, which keeps the layer usable at any zoom.
#
# World_Terrain_Base was the layer that failed this way: it is a legacy
# service with a shallow cache. World_Topo_Map replaces it — a full zoom
# range, and more useful here anyway, since it carries contours, roads and
# settlement names that matter when planning site access.
MAX_NATIVE_ZOOM = 19
MAX_ZOOM = 22

BASEMAPS = {
    "Satellite (Esri)": {
        "tiles": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        "attr": "Esri, Maxar, Earthstar Geographics",
        "default": True,
    },
    "Street (OSM)": {
        "tiles": "OpenStreetMap",
        "attr": None,
        "default": False,
    },
    "Topographic (Esri)": {
        "tiles": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
        ),
        "attr": "Esri, HERE, Garmin, Intermap, USGS",
        "default": False,
    },
}


def build_map(
    center: tuple[float, float] | None = None,
    zoom: int | None = None,
    boundary_geojson: dict | None = None,
    tile_url: str | None = None,
    show_draw: bool = True,
) -> folium.Map:
    """Assemble the map with basemaps, boundary, and optional GEE overlay."""
    center = center or config.INDIA_CENTER
    zoom = zoom or config.INDIA_ZOOM

    fmap = folium.Map(
        location=list(center),
        zoom_start=zoom,
        tiles=None,
        control_scale=True,
        max_zoom=MAX_ZOOM,
    )

    for name, spec in BASEMAPS.items():
        folium.TileLayer(
            tiles=spec["tiles"],
            attr=spec["attr"],
            name=name,
            overlay=False,
            control=True,
            show=spec["default"],
            max_native_zoom=MAX_NATIVE_ZOOM,
            max_zoom=MAX_ZOOM,
        ).add_to(fmap)

    if boundary_geojson:
        folium.GeoJson(
            boundary_geojson,
            name="Project boundary",
            style_function=lambda _: {
                "color": "#FFFFFF",
                "weight": 2.5,
                "fillOpacity": 0.0,
                "dashArray": "6,4",
            },
        ).add_to(fmap)

    if tile_url:
        folium.raster_layers.TileLayer(
            tiles=tile_url,
            attr="Google Earth Engine",
            name="ARR eligibility",
            overlay=True,
            control=True,
            opacity=0.75,
            max_native_zoom=MAX_NATIVE_ZOOM,
            max_zoom=MAX_ZOOM,
        ).add_to(fmap)

    if show_draw:
        Draw(
            export=False,
            draw_options={
                "polyline": False,
                "circle": False,
                "circlemarker": False,
                "marker": False,
                "polygon": {"allowIntersection": False, "showArea": True},
                "rectangle": {"showArea": True},
            },
            edit_options={"edit": True, "remove": True},
        ).add_to(fmap)

    folium.LayerControl(collapsed=False).add_to(fmap)

    if tile_url:
        fmap.get_root().html.add_child(folium.Element(_legend_html()))

    return fmap


def _legend_html() -> str:
    """Floating legend keyed to the eligibility class palette."""
    rows = "".join(
        f'<div style="display:flex;align-items:center;margin:3px 0;">'
        f'<span style="width:14px;height:14px;background:{colour};'
        f'display:inline-block;margin-right:7px;border:1px solid rgba(0,0,0,.3);'
        f'border-radius:2px;"></span>'
        f'<span style="font-size:12px;">{label}</span></div>'
        for colour, label in [
            (config.CLASS_PALETTE[config.CLASS_ELIGIBLE], "Eligible"),
            (config.CLASS_PALETTE[config.CLASS_FLAGGED], "Flagged for review"),
            (config.CLASS_PALETTE[config.CLASS_INELIGIBLE], "Ineligible"),
        ]
    )
    return f"""
    <div style="position:fixed;bottom:28px;left:12px;z-index:9999;
                background:rgba(255,255,255,.94);padding:9px 12px;
                border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
                font-family:system-ui,-apple-system,sans-serif;">
      <div style="font-size:12px;font-weight:600;margin-bottom:5px;">
        ARR eligibility
      </div>
      {rows}
    </div>
    """

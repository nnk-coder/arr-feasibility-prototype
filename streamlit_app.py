"""ARR Feasibility Analysis — Phase 1 (India).

Upload or draw a project boundary, run the VM0047 three-class eligibility
mask, and see which parts of the land qualify for an ARR project.

Governing principle: code computes, AI interprets. Every number on this page
comes from Earth Engine or deterministic Python. Nothing is inferred.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from app import config
from app.geometry import BoundaryError, build_boundary, load_boundary
from app.mapping import build_map

st.set_page_config(
    page_title="ARR Feasibility — India",
    page_icon="🌳",
    layout="wide",
)

# --------------------------------------------------------------- styling

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; }
      [data-testid="stMetricValue"] { font-size: 1.6rem; }
      .caveat { font-size: 0.82rem; color: #555; line-height: 1.5; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("## 🌳 ARR Feasibility Analysis — VM0047")
st.caption(
    "Phase 1 · India · Eligibility screening from public satellite datasets"
)

# --------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("### Forest definition")
    st.caption(
        "VM0047 applies the host country definition. India has two in "
        "circulation and they give different answers."
    )

    definition_name = st.selectbox(
        "Definition",
        list(config.FOREST_DEFINITIONS.keys()),
        index=list(config.FOREST_DEFINITIONS).index(
            config.DEFAULT_FOREST_DEFINITION
        ),
    )
    definition = config.FOREST_DEFINITIONS[definition_name]

    st.info(f"**{definition['canopy_pct']}% canopy** · "
            f"min {definition['min_area_ha']} ha · "
            f"{definition['min_height_m']} m height\n\n{definition['note']}")

    override = st.checkbox("Override canopy threshold", value=False)
    canopy_pct = (
        st.slider("Canopy threshold (%)", 5, 50, definition["canopy_pct"])
        if override
        else definition["canopy_pct"]
    )

    st.markdown("---")
    st.markdown("### Eligibility window")
    project_start_year = st.number_input(
        "Project start year", min_value=2020, max_value=2040, value=2026, step=1
    )
    lookback_years = st.slider(
        "Required non-forest years", 5, 20, config.DEFAULT_LOOKBACK_YEARS
    )
    st.caption(
        f"Land must have been non-forest since "
        f"**{project_start_year - lookback_years}**."
    )

    st.markdown("---")
    with st.expander("🔧 Diagnostics"):
        st.caption(
            "Where configuration is being read from. Nothing sensitive is "
            "shown — never the private key."
        )
        try:
            from app.gee_init import describe_config

            cfg = describe_config()
            if cfg["credentials_source"] == "Streamlit secrets":
                st.success("Credentials: Streamlit secrets")
            elif cfg["credentials_source"] == "not set":
                st.error("Credentials: not set")
            else:
                st.warning(f"Credentials: {cfg['credentials_source']}")
                st.caption(
                    "Not using Streamlit secrets. A committed .env file will "
                    "override the Secrets box."
                )
            st.json(cfg)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read configuration: {type(exc).__name__}")

    with st.expander("Datasets"):
        st.caption(
            f"Hansen GFC · `{config.HANSEN_ASSET.split('/')[-1]}`\n\n"
            f"ESA WorldCover · {config.WORLDCOVER_EPOCH}\n\n"
            f"SRTM 30 m · slope\n\n"
            f"WDPA · protected areas"
        )

# --------------------------------------------------------- boundary input

left, right = st.columns([1.15, 1])

with left:
    st.markdown("### 1 · Project boundary")

    tab_upload, tab_draw = st.tabs(["Upload a file", "Draw on the map"])

    boundary = None
    boundary_error = None

    with tab_upload:
        uploaded = st.file_uploader(
            "KML, KMZ, GeoJSON, or zipped Shapefile",
            type=["kml", "kmz", "geojson", "json", "zip"],
            label_visibility="collapsed",
        )
        if uploaded is not None:
            try:
                boundary = load_boundary(uploaded.name, uploaded.getvalue())
                st.session_state["boundary"] = boundary
            except BoundaryError as exc:
                boundary_error = str(exc)

    with tab_draw:
        st.caption(
            "Use the polygon or rectangle tool on the map, then press "
            "**Use drawn shape**."
        )
        if st.button("Use drawn shape"):
            drawings = st.session_state.get("map_state", {}).get("all_drawings")
            if not drawings:
                boundary_error = "No shape drawn yet. Draw one on the map first."
            else:
                from shapely.geometry import shape as shapely_shape

                try:
                    geoms = [shapely_shape(d["geometry"]) for d in drawings]
                    boundary = build_boundary(geoms, source_format="drawn")
                    st.session_state["boundary"] = boundary
                except BoundaryError as exc:
                    boundary_error = str(exc)

    if boundary_error:
        st.error(boundary_error)

    boundary = st.session_state.get("boundary", boundary)

    if boundary is not None:
        st.success(
            f"**{boundary.area_ha:,.1f} ha** · {boundary.part_count} part(s) "
            f"· {boundary.source_format}"
        )
        for warning in boundary.warnings:
            st.warning(warning)

        scale = config.scale_for_area(boundary.area_ha)
        if scale > 10:
            st.info(
                f"This area will be analysed at **{scale} m** rather than 10 m "
                "because of its size."
            )

        run = st.button(
            "🔍 Run eligibility analysis", type="primary", use_container_width=True
        )
    else:
        st.caption(
            "No boundary loaded. Upload a file or draw a shape to begin. "
            "A sample KML for Bundelkhand ships in `samples/`."
        )
        run = False

# ------------------------------------------------------------ run analysis


def _cache_key(boundary, canopy, lookback, start_year) -> str:
    """Key the cached result on everything that would change it.

    Without this, every pan or zoom of the map re-triggers the Earth Engine
    calls, because Streamlit re-runs the whole script on map interaction.
    """
    payload = json.dumps(
        {
            "geom": boundary.geojson,
            "canopy": canopy,
            "lookback": lookback,
            "start": start_year,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


if run and boundary is not None:
    key = _cache_key(boundary, canopy_pct, lookback_years, project_start_year)
    if st.session_state.get("result_key") != key:
        from app.eligibility import analyse
        from app.gee_init import GEEAuthError

        try:
            with st.spinner("Querying Earth Engine…"):
                result = analyse(
                    geojson_geometry=boundary.geojson,
                    area_ha=boundary.area_ha,
                    canopy_pct=canopy_pct,
                    lookback_years=lookback_years,
                    project_start_year=int(project_start_year),
                    forest_definition_name=definition_name,
                )
            st.session_state["result"] = result
            st.session_state["result_key"] = key
        except GEEAuthError as exc:
            st.error(f"Earth Engine authentication failed.\n\n{exc}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            from app.gee_init import safe_error

            st.error(f"Analysis failed.\n\n{safe_error(exc)}")

result = st.session_state.get("result")

# ------------------------------------------------------------------- map

with left:
    st.markdown("### 2 · Map")
    fmap = build_map(
        center=boundary.centroid if boundary else None,
        zoom=13 if boundary else None,
        boundary_geojson=boundary.geojson if boundary else None,
        tile_url=result.tile_url if result else None,
        show_draw=True,
    )
    st.session_state["map_state"] = st_folium(
        fmap,
        height=520,
        use_container_width=True,
        returned_objects=["all_drawings"],
    )

# --------------------------------------------------------------- results

with right:
    st.markdown("### 3 · Results")

    if result is None:
        st.info(
            "Load a boundary and run the analysis to see eligible area here."
        )
        st.markdown(
            """
            **What this checks**

            Land qualifies for ARR under VM0047 only if it has been
            non-forest for the full lookback period. That needs three
            datasets, not one: current land cover catches natural regrowth,
            Hansen's loss-year band catches land that was forest until
            recently, and the 2000 canopy baseline catches the rest.

            Results come back in three classes rather than two, because land
            that is merely *flagged* — wetland, steep slope, protected-area
            overlap — is often recoverable with documentation, and silently
            excluding it loses real project area.
            """
        )
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Eligible", f"{result.eligible_ha:,.0f} ha",
                  f"{result.eligible_pct:.1f}% of area")
        m2.metric("Flagged", f"{result.flagged_ha:,.0f} ha",
                  f"{result.flagged_pct:.1f}% of area")
        m3.metric("Ineligible", f"{result.ineligible_ha:,.0f} ha")

        st.caption(
            f"Upside if all flagged area clears review: "
            f"**{result.upside_ha:,.0f} ha**"
        )

        st.markdown("#### Why land was excluded or flagged")
        reasons = {
            k: v for k, v in result.reason_ha.items() if v and v > 0.05
        }
        if reasons:
            pretty = {
                "currently_forest": "Currently forest",
                "was_forest_at_baseline": "Forest at 2000 baseline",
                "forest_lost_in_lookback": "Forest lost within lookback window",
                "built_up": "Built-up",
                "permanent_water": "Permanent water",
                "mangroves": "Mangroves",
                "flag_wetland": "⚑ Herbaceous wetland (peat risk)",
                "flag_steep_slope": "⚑ Steep slope",
                "flag_protected_area": "⚑ Protected area overlap",
                "flag_loss_near_window": "⚑ Forest loss just outside window",
            }
            df = pd.DataFrame(
                [
                    {"Reason": pretty.get(k, k), "Hectares": round(v, 1)}
                    for k, v in sorted(
                        reasons.items(), key=lambda kv: -kv[1]
                    )
                ]
            )
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.caption(
                "Categories overlap — a pixel can fail several tests at once, "
                "so these do not sum to the ineligible total."
            )
        else:
            st.caption("No exclusions detected across the project area.")

        with st.expander("🔬 Test coverage (diagnostics)", expanded=False):
            st.caption(
                "Percentage of the area where each test fires, before the "
                "classes are combined. A test at 100% across every boundary "
                "you try is the signature of a threshold or dataset problem "
                "rather than a real result."
            )
            cov = result.test_coverage_pct
            cov_df = pd.DataFrame(
                [
                    {
                        "Test": name.lstrip("_").replace("_", " ").capitalize(),
                        "Coverage %": "no data" if pct is None else pct,
                        "Combined": name.startswith("_"),
                    }
                    for name, pct in cov.items()
                ]
            )
            st.dataframe(cov_df, hide_index=True, use_container_width=True)

            if all(v in (None, 0) for k, v in cov.items() if not k.startswith("_")):
                st.error(
                    "Every individual test reads zero or no data. The source "
                    "datasets are not returning values over this area."
                )

        with st.expander("Assumptions applied", expanded=False):
            st.json(result.assumptions)

        with st.expander("Limitations", expanded=True):
            for caveat in result.caveats:
                st.markdown(
                    f'<div class="caveat">• {caveat}</div>',
                    unsafe_allow_html=True,
                )

        st.download_button(
            "📥 Download result as JSON",
            json.dumps(
                {
                    "total_ha": result.total_ha,
                    "eligible_ha": result.eligible_ha,
                    "flagged_ha": result.flagged_ha,
                    "ineligible_ha": result.ineligible_ha,
                    "reason_ha": result.reason_ha,
                    "assumptions": result.assumptions,
                    "caveats": result.caveats,
                },
                indent=2,
            ),
            file_name="arr_eligibility.json",
            mime="application/json",
            use_container_width=True,
        )

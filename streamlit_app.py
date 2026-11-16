"""ARR Feasibility Analysis — Phase 2 (India).

Upload or draw one or more project boundaries, run the VM0047 three-class
eligibility mask, and see which parts of the land qualify — overall, or
parcel by parcel.

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
from app.geometry import (
    BoundaryError,
    build_boundary_from_parcels,
    parcels_from_drawings,
    parcels_from_file,
)
from app.mapping import build_map

st.set_page_config(
    page_title="ARR Feasibility — India",
    page_icon="🌳",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; }
      [data-testid="stMetricValue"] { font-size: 1.5rem; }
      .caveat { font-size: 0.82rem; color: #555; line-height: 1.5; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("## 🌳 ARR Feasibility Analysis — VM0047")
st.caption("India · Eligibility screening from public satellite datasets")

# Parcels accumulate across uploads and drawings until explicitly cleared.
st.session_state.setdefault("parcels", [])


def _clear_results() -> None:
    for key in ("result", "parcel_results", "result_key", "result_parcel_key"):
        st.session_state.pop(key, None)


def _parcel_set_key(parcels) -> str:
    """Identity of the current parcel set, for invalidating stale results."""
    payload = json.dumps(
        [[p.name, p.geojson] for p in parcels], sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------- sidebar

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

    st.info(
        f"**{definition['canopy_pct']}% canopy** · "
        f"min {definition['min_area_ha']} ha · "
        f"{definition['min_height_m']} m height\n\n{definition['note']}"
    )

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
    st.markdown("### Scoring")
    per_parcel = st.checkbox(
        "Score each parcel separately",
        value=False,
        help=(
            "Off: all parcels merged into one area — fastest. On: each parcel "
            "scored on its own so you can see which to keep or drop. Both "
            "cost a single Earth Engine call."
        ),
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
            st.json(cfg)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read configuration: {type(exc).__name__}")

    with st.expander("Datasets"):
        st.caption(
            f"Hansen GFC · `{config.HANSEN_ASSET.split('/')[-1]}`\n\n"
            f"ESA WorldCover · {config.WORLDCOVER_EPOCH}\n\n"
            f"SRTM 30 m · slope\n\nWDPA · protected areas"
        )

# ----------------------------------------------------------- parcel input

left, right = st.columns([1.15, 1])

with left:
    st.markdown("### 1 · Project parcels")

    errors: list[str] = []

    tab_upload, tab_draw = st.tabs(["Upload files", "Draw on the map"])

    with tab_upload:
        uploaded = st.file_uploader(
            "KML, KMZ, GeoJSON, or zipped Shapefile — several at once is fine",
            type=["kml", "kmz", "geojson", "json", "zip"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        if uploaded:
            already = {
                (p.source, p.name) for p in st.session_state["parcels"]
            }
            added = 0
            for upload in uploaded:
                try:
                    for parcel in parcels_from_file(
                        upload.name, upload.getvalue()
                    ):
                        if (parcel.source, parcel.name) not in already:
                            st.session_state["parcels"].append(parcel)
                            already.add((parcel.source, parcel.name))
                            added += 1
                except BoundaryError as exc:
                    errors.append(f"**{upload.name}** — {exc}")
            if added:
                _clear_results()

    with tab_draw:
        st.caption(
            "Draw with the polygon or rectangle tool, then add the shapes. "
            "They stack with anything already loaded."
        )
        if st.button("➕ Add drawn shapes", use_container_width=True):
            drawings = st.session_state.get("map_state", {}).get("all_drawings")
            if not drawings:
                errors.append("No shapes drawn yet. Draw one on the map first.")
            else:
                existing_drawn = sum(
                    1 for p in st.session_state["parcels"] if p.source == "drawn"
                )
                try:
                    new = parcels_from_drawings(
                        drawings, start_index=existing_drawn + 1
                    )
                    # Drawn shapes persist in the map widget, so re-adding
                    # would duplicate them. Match on geometry, not name.
                    seen_geoms = {
                        json.dumps(p.geojson, sort_keys=True)
                        for p in st.session_state["parcels"]
                    }
                    added = 0
                    for parcel in new:
                        sig = json.dumps(parcel.geojson, sort_keys=True)
                        if sig not in seen_geoms:
                            st.session_state["parcels"].append(parcel)
                            seen_geoms.add(sig)
                            added += 1
                    if added:
                        _clear_results()
                    else:
                        errors.append("Those shapes are already added.")
                except BoundaryError as exc:
                    errors.append(str(exc))

    for message in errors:
        st.error(message)

    parcels = st.session_state["parcels"]
    boundary = None

    if parcels:
        try:
            boundary = build_boundary_from_parcels(parcels)
        except BoundaryError as exc:
            st.error(str(exc))

    if boundary is not None:
        st.success(
            f"**{boundary.area_ha:,.1f} ha** across "
            f"**{boundary.part_count} parcel(s)** · "
            f"{', '.join(boundary.sources)}"
        )
        for warning in boundary.warnings:
            st.warning(warning)

        with st.expander(f"Parcel list ({boundary.part_count})", expanded=False):
            for idx, parcel in enumerate(boundary.parcels):
                cols = st.columns([4, 2, 1])
                cols[0].write(parcel.name)
                cols[1].write(f"{parcel.area_ha:,.1f} ha")
                if cols[2].button("✕", key=f"rm_{idx}", help="Remove"):
                    st.session_state["parcels"].pop(idx)
                    _clear_results()
                    st.rerun()

        if st.button("Clear all parcels", use_container_width=True):
            st.session_state["parcels"] = []
            _clear_results()
            st.rerun()

        scale = config.scale_for_area(boundary.area_ha)
        if scale > 10:
            st.info(
                f"This area will be analysed at **{scale} m** rather than "
                "10 m because of its size."
            )

        run = st.button(
            "🔍 Run eligibility analysis",
            type="primary",
            use_container_width=True,
        )
    else:
        st.caption(
            "No parcels loaded. Upload files or draw shapes to begin. "
            "`samples/sample_multi_parcel.kml` has three named parcels."
        )
        run = False

# ------------------------------------------------------------ run analysis

if boundary is not None:
    parcel_key = _parcel_set_key(boundary.parcels)
    settings_key = hashlib.sha256(
        json.dumps(
            {
                "canopy": canopy_pct,
                "lookback": lookback_years,
                "start": int(project_start_year),
                "per_parcel": per_parcel,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    run_key = f"{parcel_key}:{settings_key}"

    # Discard a result computed for a different parcel set or settings, so a
    # new upload never shows the previous run's overlay and hectares.
    if st.session_state.get("result_key") != run_key:
        _clear_results()
else:
    run_key = None
    _clear_results()

if run and boundary is not None:
    from app.eligibility import analyse, analyse_by_parcel
    from app.gee_init import GEEAuthError

    try:
        with st.spinner("Querying Earth Engine…"):
            if per_parcel:
                combined, parcel_results = analyse_by_parcel(
                    parcels=boundary.parcels,
                    merged_geojson=boundary.geojson,
                    total_area_ha=boundary.area_ha,
                    canopy_pct=canopy_pct,
                    lookback_years=lookback_years,
                    project_start_year=int(project_start_year),
                    forest_definition_name=definition_name,
                )
                st.session_state["parcel_results"] = parcel_results
            else:
                combined = analyse(
                    geojson_geometry=boundary.geojson,
                    area_ha=boundary.area_ha,
                    canopy_pct=canopy_pct,
                    lookback_years=lookback_years,
                    project_start_year=int(project_start_year),
                    forest_definition_name=definition_name,
                )
                st.session_state["parcel_results"] = None
        st.session_state["result"] = combined
        st.session_state["result_key"] = run_key
    except GEEAuthError as exc:
        st.error(f"Earth Engine authentication failed.\n\n{exc}")
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        from app.gee_init import safe_error

        st.error(f"Analysis failed.\n\n{safe_error(exc)}")

result = st.session_state.get("result")
parcel_results = st.session_state.get("parcel_results")

# -------------------------------------------------------------------- map

with left:
    st.markdown("### 2 · Map")
    fmap = build_map(
        center=boundary.centroid if boundary else None,
        zoom=12 if boundary else None,
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

# ---------------------------------------------------------------- results

REASON_LABELS = {
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

with right:
    st.markdown("### 3 · Results")

    if result is None:
        st.info("Load parcels and run the analysis to see eligible area here.")
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
        m1.metric(
            "Eligible",
            f"{result.eligible_ha:,.0f} ha",
            f"{result.eligible_pct:.1f}% of area",
        )
        m2.metric(
            "Flagged",
            f"{result.flagged_ha:,.0f} ha",
            f"{result.flagged_pct:.1f}% of area",
        )
        m3.metric("Ineligible", f"{result.ineligible_ha:,.0f} ha")

        st.caption(
            f"Upside if all flagged area clears review: "
            f"**{result.upside_ha:,.0f} ha** · analysed at "
            f"{result.scale_m} m"
        )

        # ---------------------------------------------- per-parcel table
        if parcel_results:
            st.markdown("#### By parcel")
            pdf = pd.DataFrame(
                [
                    {
                        "Parcel": p.name,
                        "Eligible ha": p.eligible_ha,
                        "Flagged ha": p.flagged_ha,
                        "Ineligible ha": p.ineligible_ha,
                        "Eligible %": round(p.eligible_pct, 1),
                        "Scored ha": p.scored_ha,
                    }
                    for p in parcel_results
                ]
            ).sort_values("Eligible ha", ascending=False)

            st.dataframe(
                pdf,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Eligible %": st.column_config.ProgressColumn(
                        "Eligible %", min_value=0, max_value=100, format="%.1f%%"
                    )
                },
            )

            weak = [p for p in parcel_results if p.eligible_pct < 20]
            if weak:
                st.caption(
                    f"{len(weak)} parcel(s) below 20% eligible: "
                    + ", ".join(p.name for p in weak)
                    + " — candidates to drop."
                )
        elif boundary is not None and boundary.part_count > 1:
            st.caption(
                f"{boundary.part_count} parcels were merged into one area. "
                "Turn on **Score each parcel separately** in the sidebar to "
                "see them individually."
            )

        # ------------------------------------------- filterable reasons
        st.markdown("#### Why land was excluded or flagged")

        available = {
            k: v for k, v in result.reason_ha.items() if v and v > 0.05
        }
        if available:
            ordered = sorted(available.items(), key=lambda kv: -kv[1])
            all_labels = [REASON_LABELS.get(k, k) for k, _ in ordered]

            chosen = st.multiselect(
                "Show reasons",
                options=all_labels,
                default=all_labels,
                label_visibility="collapsed",
                help="Untick a reason to remove it from the table and total.",
            )

            rows = [
                {"Reason": REASON_LABELS.get(k, k), "Hectares": round(v, 1)}
                for k, v in ordered
                if REASON_LABELS.get(k, k) in chosen
            ]
            if rows:
                st.dataframe(
                    pd.DataFrame(rows), hide_index=True, use_container_width=True
                )
                shown = sum(r["Hectares"] for r in rows)
                st.caption(
                    f"Showing {len(rows)} of {len(ordered)} reasons · "
                    f"{shown:,.1f} ha across those shown. Categories overlap — "
                    "a pixel can fail several tests, so these do not sum to "
                    "the ineligible total."
                )
            else:
                st.caption("No reasons selected.")
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
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Test": name.replace("combined_", "")
                            .replace("_", " ")
                            .capitalize(),
                            "Coverage %": "no data" if pct is None else pct,
                            "Kind": (
                                "combined result"
                                if name.startswith("combined_")
                                else "individual test"
                            ),
                        }
                        for name, pct in cov.items()
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            if all(
                v in (None, 0)
                for k, v in cov.items()
                if not k.startswith("combined_")
            ):
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

        export = {
            "total_ha": result.total_ha,
            "eligible_ha": result.eligible_ha,
            "flagged_ha": result.flagged_ha,
            "ineligible_ha": result.ineligible_ha,
            "reason_ha": result.reason_ha,
            "assumptions": result.assumptions,
            "caveats": result.caveats,
        }
        if parcel_results:
            export["parcels"] = [
                {
                    "name": p.name,
                    "source": p.source,
                    "footprint_ha": p.footprint_ha,
                    "scored_ha": p.scored_ha,
                    "eligible_ha": p.eligible_ha,
                    "flagged_ha": p.flagged_ha,
                    "ineligible_ha": p.ineligible_ha,
                }
                for p in parcel_results
            ]

        st.download_button(
            "📥 Download result as JSON",
            json.dumps(export, indent=2),
            file_name="arr_eligibility.json",
            mime="application/json",
            use_container_width=True,
        )

"""Feasibility narrative.

Governing principle: code computes, AI interprets. Every figure here is
passed in already computed. Nothing in this module calculates a number, and
the language model — when one is used at all — receives those figures as
input and is instructed never to produce its own.

The default generator is deterministic and needs no API key, so the tool
works out of the box and a missing credential can never block a deployment.
Gemini is used only when GEMINI_API_KEY is configured, and its output is
labelled as generated commentary, distinct from the computed results.
"""

from __future__ import annotations

import os

from app import config

# --------------------------------------------------------------- verdicts

VERDICT_FEASIBLE = "FEASIBLE"
VERDICT_CONDITIONAL = "CONDITIONAL"
VERDICT_MARGINAL = "MARGINAL"
VERDICT_NOT_FEASIBLE = "NOT FEASIBLE"

# Share of the project area that must be eligible for each verdict.
FEASIBLE_THRESHOLD_PCT = 50.0
CONDITIONAL_THRESHOLD_PCT = 25.0
MARGINAL_THRESHOLD_PCT = 10.0


def verdict_for(eligible_pct: float, flagged_pct: float) -> tuple[str, str]:
    """Classify a result, and say why in one sentence."""
    upside = eligible_pct + flagged_pct

    if eligible_pct >= FEASIBLE_THRESHOLD_PCT:
        return (
            VERDICT_FEASIBLE,
            f"{eligible_pct:.0f}% of the area passes every VM0047 eligibility "
            "test on current public data.",
        )
    if eligible_pct >= CONDITIONAL_THRESHOLD_PCT:
        return (
            VERDICT_CONDITIONAL,
            f"{eligible_pct:.0f}% is clearly eligible. The project is viable "
            "if the eligible area alone justifies the development cost.",
        )
    if upside >= CONDITIONAL_THRESHOLD_PCT and flagged_pct > eligible_pct:
        return (
            VERDICT_CONDITIONAL,
            f"Only {eligible_pct:.0f}% is clearly eligible, but a further "
            f"{flagged_pct:.0f}% is flagged rather than excluded. Ground-"
            "truthing the flagged area is the deciding step.",
        )
    if eligible_pct >= MARGINAL_THRESHOLD_PCT:
        return (
            VERDICT_MARGINAL,
            f"Only {eligible_pct:.0f}% is eligible. A project is possible but "
            "the usable area is small relative to the boundary assessed.",
        )
    return (
        VERDICT_NOT_FEASIBLE,
        f"{eligible_pct:.0f}% eligible is too little to support an ARR "
        "project over this boundary.",
    )


# ------------------------------------------------------- rule-based report


def _fmt(value: float, unit: str = "") -> str:
    return f"{value:,.0f}{unit}"


def build_report(
    result,
    carbon=None,
    parcel_results=None,
    location_name: str = "the project area",
) -> str:
    """Deterministic feasibility narrative built from computed figures only."""
    verdict, rationale = verdict_for(result.eligible_pct, result.flagged_pct)

    lines: list[str] = []
    lines.append(f"### Verdict: {verdict}")
    lines.append("")
    lines.append(rationale)
    lines.append("")

    # --- eligibility
    lines.append("#### Eligibility")
    lines.append("")
    lines.append(
        f"Of {_fmt(result.total_ha)} ha assessed for {location_name}, "
        f"**{_fmt(result.eligible_ha)} ha ({result.eligible_pct:.1f}%)** is "
        f"eligible, **{_fmt(result.flagged_ha)} ha ({result.flagged_pct:.1f}%)** "
        f"is flagged for review, and {_fmt(result.ineligible_ha)} ha is "
        "ineligible."
    )
    if result.flagged_ha > 0:
        lines.append("")
        lines.append(
            f"If everything flagged clears review, the usable area rises to "
            f"**{_fmt(result.upside_ha)} ha**. Flagged land is not excluded — "
            "it needs documentation or a site visit."
        )

    # --- what drove exclusions
    reasons = {k: v for k, v in result.reason_ha.items() if v and v > 0.05}
    if reasons:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        readable = ", ".join(
            f"{k.replace('flag_', '').replace('_', ' ')} ({_fmt(v)} ha)"
            for k, v in top
        )
        lines.append("")
        lines.append(f"The largest constraints are {readable}.")

    # --- land cover
    if getattr(result, "lulc_rows", None):
        dominant = result.lulc_rows[0]
        lines.append("")
        lines.append("#### Land cover")
        lines.append("")
        lines.append(
            f"The area is predominantly **{dominant['label'].lower()}** "
            f"({dominant['percent']:.0f}%). {dominant['arr_note']}"
        )
        cropland = next(
            (r for r in result.lulc_rows if r["code"] == 40), None
        )
        if cropland and cropland["percent"] >= 20:
            lines.append("")
            lines.append(
                f"Cropland covers {cropland['percent']:.0f}% of the area. "
                "Displacing cultivation creates activity-shifting leakage "
                "risk, which must be assessed and deducted under VM0047."
            )

    # --- per-parcel
    if parcel_results:
        strong = [p for p in parcel_results if p.eligible_pct >= 50]
        weak = [p for p in parcel_results if p.eligible_pct < 20]
        lines.append("")
        lines.append("#### By parcel")
        lines.append("")
        lines.append(
            f"{len(parcel_results)} parcels were scored separately. "
            f"{len(strong)} exceed 50% eligible; {len(weak)} fall below 20%."
        )
        if weak:
            lines.append("")
            lines.append(
                "Consider dropping: "
                + ", ".join(f"**{p.name}** ({p.eligible_pct:.0f}%)" for p in weak[:5])
                + "."
            )

    # --- carbon
    if carbon is not None:
        c = carbon.central
        lines.append("")
        lines.append("#### Carbon potential")
        lines.append("")
        lines.append(
            f"Over a {carbon.crediting_years}-year crediting period, the "
            f"central estimate is **{_fmt(c.total_net)} tCO₂e** net of "
            f"buffer, uncertainty and leakage deductions — about "
            f"{_fmt(c.mean_annual)} tCO₂e per year."
        )
        lines.append("")
        lines.append(
            f"The plausible range runs from {_fmt(carbon.scenarios['low'].total_net)} "
            f"to {_fmt(carbon.scenarios['high'].total_net)} tCO₂e, a spread of "
            f"{carbon.range_ratio:.1f}×. That spread is driven by survival "
            "rate and the non-permanence buffer, both of which are set by "
            "project design and a risk assessment rather than by the land."
        )
        if carbon.stocking["status"] != "adequate":
            lines.append("")
            lines.append(f"⚠ **Stocking:** {carbon.stocking['message']}")

    # --- next steps
    lines.append("")
    lines.append("#### Recommended next steps")
    lines.append("")
    steps = []
    if result.flagged_ha > 0:
        steps.append(
            f"Ground-truth the {_fmt(result.flagged_ha)} ha flagged for "
            "review — this is the largest single swing factor in the estimate."
        )
    steps.append(
        "Verify the forest definition applied here against the host country "
        "requirement for your crediting standard."
    )
    steps.append(
        "Commission a site survey covering soil, species suitability and "
        "existing land use."
    )
    steps.append(
        "Begin stakeholder consultation and confirm land tenure for the "
        "eligible area."
    )
    if verdict in (VERDICT_FEASIBLE, VERDICT_CONDITIONAL):
        steps.append("Prepare a project concept note using these figures as the basis.")
    for i, step in enumerate(steps, start=1):
        lines.append(f"{i}. {step}")

    # --- provenance
    lines.append("")
    lines.append("---")
    lines.append("")
    a = result.assumptions
    lines.append(
        f"*Forest definition: {a.get('forest_definition')} "
        f"({a.get('canopy_threshold_pct')}% canopy). "
        f"Non-forest required since {a.get('non_forest_required_from')}. "
        f"Analysed at {a.get('analysis_scale_m')} m. "
        f"Sources: Hansen GFC, ESA WorldCover {a.get('worldcover_epoch')}, "
        f"SRTM, WDPA. Indicative only — not a substitute for a ground survey.*"
    )

    return "\n".join(lines)


# ------------------------------------------------------- optional Gemini

GEMINI_MODEL = "gemini-1.5-flash"


def gemini_available() -> bool:
    """True when a Gemini key is configured and the package is installed."""
    if not _gemini_key():
        return False
    try:
        import google.generativeai  # noqa: F401

        return True
    except ImportError:
        return False


def _gemini_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    if key:
        return key
    try:
        import streamlit as st

        return str(st.secrets.get("GEMINI_API_KEY", "") or "")
    except Exception:
        return ""


def enrich_with_gemini(
    result, carbon=None, parcel_results=None, location_name: str = ""
) -> str:
    """Ask Gemini to interpret the computed figures.

    The model is given the numbers and told explicitly not to invent any. It
    adds reasoning and framing, never arithmetic. If anything fails, the
    caller falls back to the deterministic report rather than showing an
    error — commentary is a nicety, the computed figures are the product.
    """
    import google.generativeai as genai

    genai.configure(api_key=_gemini_key())
    model = genai.GenerativeModel(GEMINI_MODEL)

    facts = {
        "location": location_name,
        "total_ha": result.total_ha,
        "eligible_ha": result.eligible_ha,
        "eligible_pct": round(result.eligible_pct, 1),
        "flagged_ha": result.flagged_ha,
        "ineligible_ha": result.ineligible_ha,
        "exclusion_reasons_ha": result.reason_ha,
        "land_cover": [
            {"class": r["label"], "percent": r["percent"]}
            for r in getattr(result, "lulc_rows", [])
        ],
        "assumptions": result.assumptions,
    }
    if carbon is not None:
        facts["carbon"] = {
            "central_total_tco2e": carbon.central.total_net,
            "low_total_tco2e": carbon.scenarios["low"].total_net,
            "high_total_tco2e": carbon.scenarios["high"].total_net,
            "crediting_years": carbon.crediting_years,
            "stocking_status": carbon.stocking["status"],
        }
    if parcel_results:
        facts["parcels"] = [
            {"name": p.name, "eligible_pct": round(p.eligible_pct, 1)}
            for p in parcel_results
        ]

    prompt = f"""You are a Verra VM0047 feasibility analyst writing for a carbon
project developer.

CRITICAL RULE: every number below has been computed from satellite data and
deterministic code. Use ONLY these figures. Never invent, estimate,
recalculate or extrapolate a number. If a figure you want is not present, say
so in words instead of supplying one.

COMPUTED RESULTS:
{facts}

Write a concise feasibility assessment covering: the eligibility verdict and
what drives it; what the land cover implies for project design and leakage;
the carbon potential and why the range is as wide as it is; the main risks;
and concrete next steps.

Be direct about weaknesses. A developer needs to know what would kill this
project, not encouragement. Use markdown headings. Keep it under 500 words."""

    response = model.generate_content(prompt)
    return response.text


def generate(
    result, carbon=None, parcel_results=None, location_name: str = "the project area"
) -> tuple[str, str]:
    """Produce the narrative and say how it was generated.

    Returns (markdown, source) where source is "deterministic" or "gemini".
    """
    deterministic = build_report(result, carbon, parcel_results, location_name)

    if not gemini_available():
        return deterministic, "deterministic"

    try:
        enriched = enrich_with_gemini(result, carbon, parcel_results, location_name)
        if enriched and enriched.strip():
            return enriched, "gemini"
    except Exception:
        # Commentary is optional; the computed figures are the product.
        pass

    return deterministic, "deterministic"

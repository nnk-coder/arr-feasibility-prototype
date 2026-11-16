"""Tests for the feasibility narrative.

The property that matters: the narrative must never contain a number that was
not computed and handed to it. A language model inventing a plausible hectare
figure is the failure mode this whole architecture exists to prevent, so the
deterministic generator is checked against its inputs directly.
"""

import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, ".")

from app.narrative import (  # noqa: E402
    VERDICT_CONDITIONAL,
    VERDICT_FEASIBLE,
    VERDICT_MARGINAL,
    VERDICT_NOT_FEASIBLE,
    build_report,
    generate,
    verdict_for,
)

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


@dataclass
class FakeResult:
    """Stands in for EligibilityResult without needing Earth Engine."""

    total_ha: float = 1000.0
    eligible_ha: float = 600.0
    flagged_ha: float = 150.0
    ineligible_ha: float = 250.0
    reason_ha: dict = field(default_factory=lambda: {"currently_forest": 200.0})
    lulc_rows: list = field(default_factory=list)
    assumptions: dict = field(
        default_factory=lambda: {
            "forest_definition": "India — FSI / UNFCCC (default)",
            "canopy_threshold_pct": 10,
            "non_forest_required_from": 2016,
            "analysis_scale_m": 10,
            "worldcover_epoch": 2021,
        }
    )

    @property
    def eligible_pct(self):
        return self.eligible_ha / self.total_ha * 100

    @property
    def flagged_pct(self):
        return self.flagged_ha / self.total_ha * 100

    @property
    def upside_ha(self):
        return self.eligible_ha + self.flagged_ha


def main() -> int:
    print("── Verdict thresholds")
    cases = [
        (80, 5, VERDICT_FEASIBLE),
        (55, 10, VERDICT_FEASIBLE),
        (35, 10, VERDICT_CONDITIONAL),
        (15, 40, VERDICT_CONDITIONAL),  # flagged carries it
        (15, 2, VERDICT_MARGINAL),
        (3, 2, VERDICT_NOT_FEASIBLE),
    ]
    for elig, flag, expected in cases:
        got, why = verdict_for(elig, flag)
        check(
            got == expected,
            f"eligible={elig}% flagged={flag}% gave {got}, expected {expected}",
        )
        check(bool(why.strip()), f"no rationale for {elig}/{flag}")
    print(f"  {len(cases)} threshold cases classify correctly, each with a reason: ok")

    print("\n── Numbers in the report come only from the inputs")
    r = FakeResult()
    r.lulc_rows = [
        {"code": 30, "label": "Grassland", "percent": 45.0, "hectares": 450.0,
         "colour": "#ffff4c", "arr_note": "Commonly suitable for ARR."},
        {"code": 40, "label": "Cropland", "percent": 30.0, "hectares": 300.0,
         "colour": "#f096ff", "arr_note": "Leakage risk."},
    ]
    report = build_report(r, location_name="Test Site")

    # Build the allowed set from the inputs themselves, so the test asserts
    # the real property — every figure is either an input or a deterministic
    # function of inputs — rather than a hand-maintained list that drifts.
    def spellings(value: float) -> set[str]:
        return {f"{value:,.0f}", f"{value:.0f}", f"{value:,.1f}", f"{value:.1f}"}

    allowed: set[str] = set()
    for value in (
        r.total_ha, r.eligible_ha, r.flagged_ha, r.ineligible_ha, r.upside_ha,
        r.eligible_pct, r.flagged_pct,              # derived percentages
        *r.reason_ha.values(),
        *(row["percent"] for row in r.lulc_rows),
        *(row["hectares"] for row in r.lulc_rows),
        r.assumptions["canopy_threshold_pct"],
        r.assumptions["non_forest_required_from"],
        r.assumptions["analysis_scale_m"],
        r.assumptions["worldcover_epoch"],
    ):
        allowed |= spellings(value)

    # Step numbers in the ordered list are presentational, not data.
    allowed |= {str(i) for i in range(1, 10)}
    # Match standalone numbers only. Without the lookarounds this also
    # catches "0047" inside "VM0047" and swallows trailing punctuation,
    # producing false positives rather than real leaks.
    found = set(
        re.findall(r"(?<![A-Za-z0-9])\d[\d,]*\d(?![A-Za-z0-9])", report)
    )
    unexplained = found - allowed
    check(
        not unexplained,
        f"report contains numbers not traceable to inputs: {sorted(unexplained)}",
    )
    print(f"  {len(found)} multi-digit figures, all traceable to inputs: ok")

    print("\n── Report structure")
    for section in ("Verdict", "Eligibility", "Land cover", "Recommended next steps"):
        check(section in report, f"report is missing the {section!r} section")
    check(
        "Forest definition:" in report,
        "report does not state the forest definition applied",
    )
    check(
        "not a substitute for a ground survey" in report,
        "report does not carry its indicative-only caveat",
    )
    print("  sections, forest definition and caveat all present: ok")

    print("\n── Cropland leakage warning fires")
    check("leakage" in report.lower(), "30% cropland did not trigger a leakage note")
    r_nocrop = FakeResult()
    r_nocrop.lulc_rows = [
        {"code": 30, "label": "Grassland", "percent": 95.0, "hectares": 950.0,
         "colour": "#ffff4c", "arr_note": "Commonly suitable for ARR."}
    ]
    check(
        "leakage" not in build_report(r_nocrop).lower(),
        "leakage note fired with no cropland present",
    )
    print("  fires at 30% cropland, silent at 0%: ok")

    print("\n── Degenerate inputs")
    empty = FakeResult(
        total_ha=100.0, eligible_ha=0.0, flagged_ha=0.0, ineligible_ha=100.0,
        reason_ha={},
    )
    out = build_report(empty)
    check(VERDICT_NOT_FEASIBLE in out, "all-ineligible area not called not feasible")
    check(len(out) > 200, "degenerate report is suspiciously short")
    print("  all-ineligible area produces a complete NOT FEASIBLE report: ok")

    print("\n── generate() falls back without an API key")
    text, source = generate(r, location_name="Test Site")
    check(
        source == "deterministic",
        f"expected deterministic fallback with no key, got {source!r}",
    )
    check(bool(text.strip()), "generate returned empty text")
    print("  no GEMINI_API_KEY -> deterministic report, no error: ok")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print("\nNarrative is traceable to its inputs and degrades safely.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

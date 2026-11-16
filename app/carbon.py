"""Carbon credit estimator.

Pure Python — no Earth Engine, no language model. Takes eligible hectares and
user inputs, returns a low / central / high credit trajectory.

Two methodological points drive the design, both places where feasibility
estimates commonly go wrong:

1. The rate is per HECTARE, so planting density cannot multiply into the
   total without double counting. Density instead drives a stocking adequacy
   check and a yield scalar against the density the rate factor assumes.

2. Published per-hectare factors are usually a MEAN ANNUAL INCREMENT averaged
   over the growth period, which already absorbs the slow establishment
   years. Applying a growth curve on top of an MAI figure discounts the
   ramp-up twice and materially understates the project. The curve therefore
   only applies when the user supplies a peak (mature) rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------- rate types

RATE_MAI = "Mean annual increment (default)"
RATE_PEAK = "Peak / mature annual rate"

RATE_TYPES = {
    RATE_MAI: {
        "applies_growth_curve": False,
        "help": (
            "The rate is an average across the growth period, so the slow "
            "early years are already included. No growth curve is applied — "
            "adding one would discount establishment twice."
        ),
    },
    RATE_PEAK: {
        "applies_growth_curve": True,
        "help": (
            "The rate describes a mature stand. A Chapman-Richards curve "
            "discounts the establishment years so the project only reaches "
            "this rate as the stand matures."
        ),
    },
}

# ------------------------------------------------------------ growth curve

# Chapman-Richards shape parameters for the establishment ramp.
# With k=0.25, p=2 the stand reaches roughly 5% of mature rate in year 1,
# 51% by year 5, 84% by year 10 and 99% by year 20 — a realistic
# establishment profile for ARR planting.
GROWTH_K = 0.25
GROWTH_P = 2.0


def growth_factor(year: int, applies_curve: bool) -> float:
    """Fraction of the mature annual rate achieved in a given year."""
    if not applies_curve:
        return 1.0
    if year < 1:
        return 0.0
    return (1.0 - math.exp(-GROWTH_K * year)) ** GROWTH_P


# ------------------------------------------------------------- stocking

# Typical minimum stocking for land to qualify as reforestation. Below this,
# the stand may never meet the forest definition at maturity, which
# undermines the project regardless of the carbon arithmetic.
MIN_STOCKING_PER_HA = 400
HIGH_STOCKING_PER_HA = 2500

# The planting density a published per-hectare factor typically assumes.
DEFAULT_REFERENCE_DENSITY = 1100


def stocking_assessment(density: int, survival_pct: float) -> dict:
    """Judge whether a planting density is viable, independent of carbon."""
    effective = density * (survival_pct / 100.0)

    if effective < MIN_STOCKING_PER_HA:
        status = "inadequate"
        message = (
            f"Effective stocking of {effective:,.0f} trees/ha after "
            f"{survival_pct:.0f}% survival is below the ~{MIN_STOCKING_PER_HA} "
            "trees/ha typically needed for land to qualify as reforestation. "
            "The stand may never meet the forest definition at maturity."
        )
    elif density > HIGH_STOCKING_PER_HA:
        status = "high"
        message = (
            f"Planting density of {density:,} trees/ha is high. Expect "
            "competition for light and water, and mortality above the "
            "survival rate assumed here."
        )
    else:
        status = "adequate"
        message = (
            f"Effective stocking of {effective:,.0f} trees/ha after "
            f"{survival_pct:.0f}% survival is within the normal range for ARR."
        )

    return {
        "status": status,
        "message": message,
        "effective_stocking": round(effective, 0),
    }


# ------------------------------------------------------------- scenarios

@dataclass
class Scenario:
    """One point of the low / central / high range."""

    name: str
    survival_pct: float
    buffer_pct: float
    uncertainty_pct: float
    leakage_pct: float


# Buffer percentages follow the VCS AFOLU Non-Permanence Risk Tool, where the
# actual figure comes from a project-specific risk assessment. This band spans
# the plausible outcomes of that assessment.
SCENARIOS = {
    "low": Scenario("Low", survival_pct=65, buffer_pct=25, uncertainty_pct=15, leakage_pct=10),
    "central": Scenario("Central", survival_pct=80, buffer_pct=15, uncertainty_pct=10, leakage_pct=5),
    "high": Scenario("High", survival_pct=92, buffer_pct=10, uncertainty_pct=5, leakage_pct=2),
}

# Non-forest land typically accrues some biomass through natural
# regeneration, so the ARR baseline is small but not zero.
DEFAULT_BASELINE_PCT = 5.0


@dataclass
class ScenarioResult:
    """Credit trajectory under one scenario."""

    name: str
    survival_pct: float
    buffer_pct: float
    uncertainty_pct: float
    leakage_pct: float
    density_scalar: float
    annual_net: list[float] = field(default_factory=list)
    cumulative_net: list[float] = field(default_factory=list)

    @property
    def total_net(self) -> float:
        return self.cumulative_net[-1] if self.cumulative_net else 0.0

    @property
    def mean_annual(self) -> float:
        return self.total_net / len(self.annual_net) if self.annual_net else 0.0


@dataclass
class CarbonResult:
    """Everything the estimator produces."""

    eligible_ha: float
    rate_per_ha_yr: float
    rate_type: str
    crediting_years: int
    density: int
    reference_density: int
    stocking: dict
    scenarios: dict[str, ScenarioResult]
    assumptions: dict

    @property
    def central(self) -> ScenarioResult:
        return self.scenarios["central"]

    @property
    def range_ratio(self) -> float:
        """How far apart the ends of the range sit."""
        low = self.scenarios["low"].total_net
        high = self.scenarios["high"].total_net
        return (high / low) if low else 0.0


def density_scalar(
    density: int, survival_pct: float, reference_density: int
) -> float:
    """Yield multiplier from planting density relative to the reference.

    Survival is folded in here rather than applied separately, which avoids
    discounting it twice — once through stocking and again through yield.

    Capped at 1.0 deliberately: over-planting does not raise per-hectare yield
    proportionally, it mostly raises mortality.
    """
    if reference_density <= 0:
        return 1.0
    effective = density * (survival_pct / 100.0)
    return min(effective / reference_density, 1.0)


def estimate(
    eligible_ha: float,
    rate_per_ha_yr: float,
    rate_type: str = RATE_MAI,
    crediting_years: int = 30,
    density: int = 1100,
    reference_density: int = DEFAULT_REFERENCE_DENSITY,
    baseline_pct: float = DEFAULT_BASELINE_PCT,
) -> CarbonResult:
    """Run the estimate across all three scenarios."""
    applies_curve = RATE_TYPES.get(rate_type, RATE_TYPES[RATE_MAI])[
        "applies_growth_curve"
    ]

    results: dict[str, ScenarioResult] = {}

    for key, scenario in SCENARIOS.items():
        scalar = density_scalar(
            density, scenario.survival_pct, reference_density
        )

        annual_net: list[float] = []
        cumulative_net: list[float] = []
        running = 0.0

        for year in range(1, crediting_years + 1):
            gross = (
                eligible_ha
                * rate_per_ha_yr
                * scalar
                * growth_factor(year, applies_curve)
            )
            baseline = gross * (baseline_pct / 100.0)
            leakage = gross * (scenario.leakage_pct / 100.0)

            net = (gross - baseline - leakage)
            net *= 1.0 - scenario.uncertainty_pct / 100.0
            net *= 1.0 - scenario.buffer_pct / 100.0
            net = max(net, 0.0)

            # Round BEFORE accumulating, so the cumulative series is exactly
            # the running sum of the annual series as displayed. Accumulating
            # unrounded values and rounding only for display leaves the two
            # columns disagreeing by a growing margin — a reader who adds up
            # the annual column gets a different total from the one shown,
            # which is corrosive in a document meant to be auditable.
            net = round(net, 1)
            running = round(running + net, 1)
            annual_net.append(net)
            cumulative_net.append(running)

        results[key] = ScenarioResult(
            name=scenario.name,
            survival_pct=scenario.survival_pct,
            buffer_pct=scenario.buffer_pct,
            uncertainty_pct=scenario.uncertainty_pct,
            leakage_pct=scenario.leakage_pct,
            density_scalar=round(scalar, 3),
            annual_net=annual_net,
            cumulative_net=cumulative_net,
        )

    return CarbonResult(
        eligible_ha=round(eligible_ha, 1),
        rate_per_ha_yr=rate_per_ha_yr,
        rate_type=rate_type,
        crediting_years=crediting_years,
        density=density,
        reference_density=reference_density,
        stocking=stocking_assessment(density, SCENARIOS["central"].survival_pct),
        scenarios=results,
        assumptions={
            "rate_per_ha_yr": rate_per_ha_yr,
            "rate_type": rate_type,
            "growth_curve_applied": applies_curve,
            "growth_curve": (
                f"Chapman-Richards k={GROWTH_K}, p={GROWTH_P}"
                if applies_curve
                else "none — rate already averages the growth period"
            ),
            "crediting_years": crediting_years,
            "planting_density_per_ha": density,
            "reference_density_per_ha": reference_density,
            "baseline_deduction_pct": baseline_pct,
            "buffer_source": "VCS AFOLU Non-Permanence Risk Tool range",
            "scenarios": {
                k: {
                    "survival_pct": s.survival_pct,
                    "buffer_pct": s.buffer_pct,
                    "uncertainty_pct": s.uncertainty_pct,
                    "leakage_pct": s.leakage_pct,
                }
                for k, s in SCENARIOS.items()
            },
        },
    )

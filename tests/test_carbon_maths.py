"""Tests for the carbon credit estimator.

Pure arithmetic, no dependencies. The properties checked here are the ones
that would silently produce a wrong number rather than an error — the kind
that ends up quoted in an investment memo.
"""

import sys

sys.path.insert(0, ".")

from app.carbon import (  # noqa: E402
    GROWTH_K,
    GROWTH_P,
    MIN_STOCKING_PER_HA,
    RATE_MAI,
    RATE_PEAK,
    density_scalar,
    estimate,
    growth_factor,
    stocking_assessment,
)

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    print("── Growth curve")

    # MAI must never apply a curve. This is the double-discounting guard.
    for year in (1, 5, 10, 30):
        check(
            growth_factor(year, applies_curve=False) == 1.0,
            f"MAI mode applied a growth factor in year {year} — this would "
            "discount establishment twice",
        )
    print("  MAI mode applies no curve in any year: ok")

    # Peak mode must ramp from near zero to near one, monotonically.
    curve = [growth_factor(y, True) for y in range(1, 31)]
    check(curve[0] < 0.15, f"year 1 factor {curve[0]:.3f} is too high for establishment")
    check(curve[-1] > 0.95, f"year 30 factor {curve[-1]:.3f} never reaches maturity")
    check(
        all(b >= a for a, b in zip(curve, curve[1:])),
        "growth curve is not monotonically increasing",
    )
    check(
        all(0.0 <= v <= 1.0 for v in curve),
        "growth factor left the 0-1 range",
    )
    print(
        f"  Peak mode ramps {curve[0]:.1%} (yr 1) -> {curve[4]:.1%} (yr 5) -> "
        f"{curve[9]:.1%} (yr 10) -> {curve[19]:.1%} (yr 20): ok"
    )

    print("\n── Density scalar")

    # Capped at 1.0: over-planting must not inflate yield.
    check(
        density_scalar(5000, 100, 1100) == 1.0,
        "density scalar exceeded 1.0 — over-planting inflated yield",
    )
    # Survival folded in, not applied twice.
    s = density_scalar(1100, 50, 1100)
    check(
        abs(s - 0.5) < 1e-9,
        f"expected scalar 0.5 at half survival, got {s}",
    )
    # Below reference scales down proportionally.
    s2 = density_scalar(550, 100, 1100)
    check(abs(s2 - 0.5) < 1e-9, f"expected 0.5 at half density, got {s2}")
    print("  capped at 1.0, survival folded in once, scales proportionally: ok")

    print("\n── Stocking assessment")
    low = stocking_assessment(300, 80)
    check(low["status"] == "inadequate", "300 trees/ha at 80% survival not flagged")
    ok = stocking_assessment(1100, 80)
    check(ok["status"] == "adequate", "1100 trees/ha at 80% survival wrongly flagged")
    high = stocking_assessment(3000, 80)
    check(high["status"] == "high", "3000 trees/ha not flagged as high")
    print(
        f"  inadequate <{MIN_STOCKING_PER_HA} effective, adequate mid, "
        "high above threshold: ok"
    )

    print("\n── Estimate: ordering and sanity")
    r = estimate(
        eligible_ha=1000,
        rate_per_ha_yr=10.0,
        rate_type=RATE_MAI,
        crediting_years=30,
        density=1100,
        reference_density=1100,
    )

    lo = r.scenarios["low"].total_net
    mid = r.scenarios["central"].total_net
    hi = r.scenarios["high"].total_net

    check(lo < mid < hi, f"scenarios out of order: low={lo}, central={mid}, high={hi}")
    check(lo > 0, "low scenario produced zero or negative credits")

    # Every scenario must be strictly below the undeducted gross, or the
    # deduction stack is not being applied.
    gross = 1000 * 10.0 * 30
    check(hi < gross, f"high scenario {hi:,.0f} is not below gross {gross:,.0f}")
    print(f"  low {lo:,.0f} < central {mid:,.0f} < high {hi:,.0f} < gross {gross:,.0f}: ok")
    print(f"  range ratio high/low = {r.range_ratio:.2f}x")

    # Cumulative must be the running sum of annual, and monotonic.
    for key, sc in r.scenarios.items():
        running = 0.0
        for i, annual in enumerate(sc.annual_net):
            running += annual
            if abs(running - sc.cumulative_net[i]) > 1e-9:
                failures.append(
                    f"{key}: cumulative[{i}] = {sc.cumulative_net[i]} but "
                    f"running sum is {running:.1f}"
                )
                break
        check(
            all(b >= a for a, b in zip(sc.cumulative_net, sc.cumulative_net[1:])),
            f"{key}: cumulative series is not monotonic",
        )
    print("  cumulative equals running sum of annual EXACTLY, monotonic: ok")

    print("\n── MAI vs peak: the double-discounting guard")
    mai = estimate(1000, 10.0, RATE_MAI, 30, 1100, 1100)
    peak = estimate(1000, 10.0, RATE_PEAK, 30, 1100, 1100)

    check(
        peak.central.total_net < mai.central.total_net,
        "peak mode did not discount establishment relative to MAI",
    )
    # MAI must be perfectly linear — constant annual under a flat curve.
    annual = mai.central.annual_net
    check(
        max(annual) - min(annual) < 0.5,
        f"MAI annual series is not flat: min={min(annual)}, max={max(annual)}",
    )
    # Peak must start well below its own mature rate.
    p_annual = peak.central.annual_net
    check(
        p_annual[0] < p_annual[-1] * 0.25,
        f"peak mode year 1 ({p_annual[0]}) is not well below year 30 "
        f"({p_annual[-1]})",
    )
    ratio = mai.central.total_net / peak.central.total_net
    print(f"  MAI total {mai.central.total_net:,.0f} vs peak {peak.central.total_net:,.0f}")
    print(f"  MAI annual is flat; peak yr1 {p_annual[0]:,.0f} -> yr30 {p_annual[-1]:,.0f}: ok")
    print(f"  applying a curve to an MAI rate would understate by {ratio:.2f}x")

    print("\n── Edge cases")
    zero = estimate(0, 10.0, RATE_MAI, 30, 1100, 1100)
    check(zero.central.total_net == 0, "zero eligible area produced credits")

    short = estimate(100, 5.0, RATE_MAI, 1, 1100, 1100)
    check(len(short.central.annual_net) == 1, "crediting years not respected")

    noref = density_scalar(1100, 80, 0)
    check(noref == 1.0, "zero reference density did not fall back to 1.0")

    nonneg = estimate(100, 0.0, RATE_MAI, 30, 1100, 1100)
    check(nonneg.central.total_net == 0, "zero rate produced credits")
    print("  zero area, zero rate, 1-year period, zero reference: ok")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print("\nCarbon maths holds: ordering, deductions, and no double discounting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

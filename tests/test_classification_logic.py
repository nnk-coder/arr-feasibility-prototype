"""Exhaustive test of the eligibility class algebra.

The GEE calls cannot run without credentials, but the boolean combination that
assigns each pixel to eligible / flagged / ineligible is pure logic and is the
part most likely to be wrong. Overlapping classes would silently double-count
hectares, which is the worst failure this tool could have: it would overstate
eligible area without erroring.

This mirrors the combination in eligibility.build_masks() and checks it over
every possible combination of input conditions.
"""

import itertools
import sys

# The eleven independent per-pixel conditions, in the same order used below.
HARD = [
    "currently_forest",
    "was_forest_2000",
    "lost_in_window",
    "built_up",
    "permanent_water",
    "snow_ice",
    "mangroves",
]
FLAG = [
    "wetland",
    "steep",
    "loss_near_boundary",
    "protected",
]

CLASS_INELIGIBLE, CLASS_ELIGIBLE, CLASS_FLAGGED = 0, 1, 2


def classify(cond: dict) -> tuple[bool, bool, bool, int]:
    """Mirror of the mask combination in eligibility.build_masks()."""
    ineligible = any(cond[name] for name in HARD)
    flagged = any(cond[name] for name in FLAG) and not ineligible
    eligible = (not ineligible) and (not flagged)

    # Mirror of the arithmetic class assignment in eligibility.build_masks():
    #   eligible * 1  +  flagged * 2
    # This is only correct because the classes are mutually exclusive — if
    # both were ever true it would produce 3. The exclusivity check below is
    # therefore what makes this form safe, not a nicety.
    value = eligible * CLASS_ELIGIBLE + flagged * CLASS_FLAGGED

    return eligible, flagged, ineligible, value


def main() -> int:
    names = HARD + FLAG
    failures = []
    counts = {CLASS_INELIGIBLE: 0, CLASS_ELIGIBLE: 0, CLASS_FLAGGED: 0}

    for combo in itertools.product([False, True], repeat=len(names)):
        cond = dict(zip(names, combo))
        eligible, flagged, ineligible, value = classify(cond)

        # Property 1: mutually exclusive and exhaustive.
        active = sum([eligible, flagged, ineligible])
        if active != 1:
            failures.append(
                f"{active} classes active (expected exactly 1) for {cond}"
            )

        # Property 2: the class code matches the boolean that is set.
        expected = (
            CLASS_ELIGIBLE if eligible
            else CLASS_FLAGGED if flagged
            else CLASS_INELIGIBLE
        )
        if value != expected:
            failures.append(f"class code {value} != {expected} for {cond}")

        # Property 3: any hard condition forces ineligible, whatever else is set.
        if any(cond[n] for n in HARD) and not ineligible:
            failures.append(f"hard condition did not force ineligible: {cond}")

        # Property 4: a flag condition with no hard condition forces flagged,
        # never eligible. This is the one that would overstate eligible area.
        if any(cond[n] for n in FLAG) and not any(cond[n] for n in HARD):
            if not flagged:
                failures.append(f"flag condition did not force flagged: {cond}")

        # Property 5: eligible only when nothing at all is set.
        if eligible and any(combo):
            failures.append(f"eligible despite active conditions: {cond}")

        # Property 6: the arithmetic form never produces an out-of-range
        # class. A 3 would mean eligible and flagged were both true, which
        # the GEE code would silently render as an unmapped palette index.
        if value not in (CLASS_INELIGIBLE, CLASS_ELIGIBLE, CLASS_FLAGGED):
            failures.append(
                f"arithmetic produced class {value}, outside 0-2, for {cond}"
            )

        counts[value] += 1

    total = 2 ** len(names)
    print(f"combinations tested : {total:,}")
    print(f"  -> eligible       : {counts[CLASS_ELIGIBLE]:,}")
    print(f"  -> flagged        : {counts[CLASS_FLAGGED]:,}")
    print(f"  -> ineligible     : {counts[CLASS_INELIGIBLE]:,}")

    # Sanity: exactly one combination (all false) should yield eligible.
    if counts[CLASS_ELIGIBLE] != 1:
        failures.append(
            f"expected exactly 1 eligible combination, got {counts[CLASS_ELIGIBLE]}"
        )

    # Sanity: flagged count is combinations where some flag is set and no hard
    # condition is — that is (2^4 - 1) * 1.
    expected_flagged = (2 ** len(FLAG) - 1)
    if counts[CLASS_FLAGGED] != expected_flagged:
        failures.append(
            f"expected {expected_flagged} flagged combinations, "
            f"got {counts[CLASS_FLAGGED]}"
        )

    if failures:
        print(f"\nFAILED — {len(failures)} problem(s):")
        for f in failures[:10]:
            print(f"  {f}")
        return 1

    print("\nAll properties hold: classes are mutually exclusive and exhaustive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

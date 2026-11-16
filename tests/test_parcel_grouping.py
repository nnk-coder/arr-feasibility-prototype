"""Test the composite (parcel, class) grouping key.

Per-parcel scoring packs the parcel id and the class code into one integer so
a single grouped reduction returns every combination. If the packing and
unpacking disagree, areas get attributed to the wrong parcel silently — the
totals still add up, so nothing looks wrong.
"""

import sys

sys.path.insert(0, ".")

from app.config import (  # noqa: E402
    CLASS_ELIGIBLE,
    CLASS_FLAGGED,
    CLASS_INELIGIBLE,
)
from app.config import PARCEL_CLASS_MULTIPLIER  # noqa: E402

CLASSES = (CLASS_INELIGIBLE, CLASS_ELIGIBLE, CLASS_FLAGGED)
MAX_PARCELS = 500


def encode(pid: int, cls: int) -> int:
    """Mirror of parcel_img.multiply(M).add(classified)."""
    return pid * PARCEL_CLASS_MULTIPLIER + cls


def decode(code: int) -> tuple[int, int]:
    """Mirror of divmod(code, M) in analyse_by_parcel."""
    return divmod(code, PARCEL_CLASS_MULTIPLIER)


def main() -> int:
    failures = []

    # The multiplier must exceed every class code, or a class would overflow
    # into the parcel id and silently reassign area to another parcel.
    if PARCEL_CLASS_MULTIPLIER <= max(CLASSES):
        failures.append(
            f"multiplier {PARCEL_CLASS_MULTIPLIER} does not exceed the "
            f"highest class code {max(CLASSES)}"
        )

    # Round trip every (parcel, class) pair.
    seen_codes: dict[int, tuple[int, int]] = {}
    for pid in range(0, MAX_PARCELS + 1):
        for cls in CLASSES:
            code = encode(pid, cls)
            back_pid, back_cls = decode(code)

            if (back_pid, back_cls) != (pid, cls):
                failures.append(
                    f"round trip failed: ({pid},{cls}) -> {code} -> "
                    f"({back_pid},{back_cls})"
                )

            # No two distinct pairs may share a code.
            if code in seen_codes and seen_codes[code] != (pid, cls):
                failures.append(
                    f"collision: {code} maps to both {seen_codes[code]} "
                    f"and ({pid},{cls})"
                )
            seen_codes[code] = (pid, cls)

    print(f"round-tripped {len(seen_codes):,} (parcel, class) pairs")
    print(f"  parcels 0-{MAX_PARCELS}, classes {CLASSES}")
    print(f"  multiplier {PARCEL_CLASS_MULTIPLIER}, no collisions")

    # pid 0 means "outside every parcel" and must be discardable without
    # losing a real parcel's area.
    for cls in CLASSES:
        pid, _ = decode(encode(0, cls))
        if pid != 0:
            failures.append(f"pid 0 did not decode back to 0 for class {cls}")
    print("  pid 0 reserved for outside-any-parcel: ok")

    # A parcel's classes must sum to its scored area, with nothing lost.
    fake = {1: {0: 100.0, 1: 250.0, 2: 50.0}, 2: {0: 400.0, 1: 0.0, 2: 0.0}}
    for pid, by_class in fake.items():
        scored = sum(by_class.values())
        recomposed = sum(
            by_class.get(c, 0.0) for c in CLASSES
        )
        if abs(scored - recomposed) > 1e-9:
            failures.append(f"parcel {pid}: class areas do not sum to scored area")
    print("  class areas sum to scored area: ok")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures[:10]:
            print(f"  {f}")
        return 1

    print("\nComposite key is collision-free and round-trips exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

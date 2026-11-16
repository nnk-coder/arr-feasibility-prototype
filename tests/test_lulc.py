"""Tests for land cover summarisation.

No dependencies — the palette, labels and percentage arithmetic are plain
Python, so these run on a bare checkout.
"""

import sys

sys.path.insert(0, ".")

from app.config import WORLDCOVER_LABELS  # noqa: E402
from app.lulc import (  # noqa: E402
    ARR_RELEVANCE,
    WORLDCOVER_PALETTE,
    class_codes,
    dominant_class,
    palette_list,
    summarise,
)

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    print("── Palette and labels are complete")
    for code in WORLDCOVER_PALETTE:
        check(code in WORLDCOVER_LABELS, f"class {code} has no label")
        check(code in ARR_RELEVANCE, f"class {code} has no ARR note")
        colour = WORLDCOVER_PALETTE[code]
        check(
            colour.startswith("#") and len(colour) == 7,
            f"class {code} colour {colour!r} is not a 6-digit hex",
        )
    check(
        len(palette_list()) == len(class_codes()),
        "palette list and class codes disagree in length",
    )
    print(f"  {len(WORLDCOVER_PALETTE)} classes, each with label, note and hex colour: ok")

    print("\n── Summarisation arithmetic")
    hist = {"10": 120000.0, "30": 410000.0, "40": 380000.0, "50": 22000.0, "60": 68000.0}
    rows = summarise(hist, total_ha=1000.0)

    check(len(rows) == 5, f"expected 5 rows, got {len(rows)}")
    total_ha = sum(r["hectares"] for r in rows)
    total_pct = sum(r["percent"] for r in rows)
    check(abs(total_ha - 1000.0) < 0.5, f"hectares sum to {total_ha}, not 1000")
    check(abs(total_pct - 100.0) < 0.5, f"percentages sum to {total_pct}, not 100")
    check(
        all(a["hectares"] >= b["hectares"] for a, b in zip(rows, rows[1:])),
        "rows are not ordered by area descending",
    )
    check(dominant_class(rows)["code"] == 30, "dominant class is wrong")
    print(f"  5 classes sum to {total_ha:,.1f} ha / {total_pct:.1f}%, ordered, dominant correct: ok")

    print("\n── Degenerate inputs")
    check(summarise({}, 100) == [], "empty histogram did not return an empty list")
    check(summarise({"10": 0.0}, 100) == [], "zero-pixel histogram did not return empty")
    check(dominant_class([]) is None, "dominant_class of empty list is not None")

    # GEE sometimes returns unexpected keys; these must be skipped, not crash.
    messy = summarise({"10": 100.0, "bogus": 5.0, "": 1.0}, total_ha=50.0)
    check(len(messy) == 1, f"malformed keys were not skipped: {messy}")
    check(abs(messy[0]["hectares"] - 50.0) < 0.1, "area wrong after skipping bad keys")
    print("  empty, zero-pixel and malformed histograms handled: ok")

    print("\n── Unknown class codes degrade gracefully")
    unknown = summarise({"999": 100.0}, total_ha=10.0)
    check(len(unknown) == 1, "unknown class code was dropped")
    check("999" in unknown[0]["label"], "unknown class has no fallback label")
    check(unknown[0]["colour"] == "#888888", "unknown class has no fallback colour")
    print("  unknown codes get a fallback label and colour: ok")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print("\nLand cover summarisation is complete and arithmetically sound.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

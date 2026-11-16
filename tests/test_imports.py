"""Import every module in app/.

This exists because a dataclass with a defaulted field ahead of a
non-defaulted one shipped to production. It is valid syntax, so py_compile
passed; it fails when the @dataclass decorator runs, which is import time.

Nothing else in the suite imported app/eligibility.py — the app imports it
lazily inside the run block, so the Streamlit smoke test never reached it,
and an earlier refactor moved the last two symbols the tests pulled from it
into other modules. The module was compiled on every run and imported on
none.

Importing everything is the cheapest guard against that whole class of
error: bad field ordering, a typo in a module-level constant, a circular
import, a decorator that throws.
"""

import importlib
import pathlib
import sys
import traceback

sys.path.insert(0, ".")

APP_DIR = pathlib.Path("app")
SKIP = {"__init__"}


def module_names() -> list[str]:
    return sorted(
        p.stem
        for p in APP_DIR.glob("*.py")
        if p.stem not in SKIP and not p.stem.startswith("_")
    )


def main() -> int:
    names = module_names()
    if not names:
        print(f"FAILED - found no modules under {APP_DIR}/")
        return 1

    failures: list[str] = []
    print(f"Importing {len(names)} modules from {APP_DIR}/:\n")

    for name in names:
        try:
            importlib.import_module(f"app.{name}")
            print(f"  ok      app.{name}")
        except Exception as exc:
            failures.append(f"app.{name}: {type(exc).__name__}: {exc}")
            print(f"  FAILED  app.{name}  ->  {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)

    # The dataclasses are the specific thing that broke, so construct each one
    # to prove its generated __init__ is usable, not merely definable.
    print("\nConstructing result dataclasses:")
    try:
        from app.carbon import estimate
        from app.eligibility import EligibilityResult, ParcelResult

        r = EligibilityResult(
            total_ha=100.0,
            eligible_ha=60.0,
            flagged_ha=10.0,
            ineligible_ha=30.0,
            reason_ha={},
            test_coverage_pct={},
            scale_m=10,
            tile_url="https://example.com/{z}/{x}/{y}",
        )
        assert abs(r.eligible_pct - 60.0) < 1e-9, "eligible_pct computed wrongly"
        assert abs(r.upside_ha - 70.0) < 1e-9, "upside_ha computed wrongly"
        assert r.lulc_rows == [], "lulc_rows did not default to an empty list"
        print("  ok      EligibilityResult (defaults and properties)")

        p = ParcelResult(
            name="P1", source="test", footprint_ha=50.0, scored_ha=50.0,
            eligible_ha=25.0, flagged_ha=5.0, ineligible_ha=20.0,
        )
        assert abs(p.eligible_pct - 50.0) < 1e-9, "parcel eligible_pct wrong"
        print("  ok      ParcelResult")

        c = estimate(100.0, 8.0)
        assert c.central.total_net > 0, "carbon estimate produced nothing"
        print("  ok      CarbonResult (via estimate)")
    except Exception as exc:
        failures.append(f"dataclass construction: {type(exc).__name__}: {exc}")
        print(f"  FAILED  {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=3)

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print(f"\nAll {len(names)} modules import and every result type constructs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

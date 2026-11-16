"""Validate every Earth Engine band name used in the eligibility engine.

Earth Engine only reports an invalid band name when the graph is evaluated on
the server, so a bad name ships cleanly and fails in production. This reads
the actual dictionary keys out of the source and checks them locally, which
needs no Earth Engine connection.
"""

import ast
import pathlib
import sys

sys.path.insert(0, ".")

from app.validation import check_band_name  # noqa: E402

SOURCE = pathlib.Path("app/eligibility.py")

# The dictionaries whose keys become band names via .rename().
BAND_NAME_DICTS = {"raw_tests", "reasons"}


def band_names_in_source() -> list[str]:
    """Pull the keys of the band-name dictionaries out of the AST."""
    tree = ast.parse(SOURCE.read_text())
    names: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        # Find a dict literal assigned to one of our known keys inside the
        # returned structure, e.g. "raw_tests": { ... }
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.Dict):
                continue
            for key, value in zip(parent.keys, parent.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in BAND_NAME_DICTS
                    and isinstance(value, ast.Dict)
                ):
                    for k in value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            names.append(k.value)
        break  # one pass is enough

    return sorted(set(names))


def main() -> int:
    failures = []

    names = band_names_in_source()
    if not names:
        print("FAILED - found no band names in the source; parser is wrong")
        return 1

    print(f"Checking {len(names)} band names from {SOURCE}:\n")
    for name in names:
        try:
            check_band_name(name)
            print(f"  ok      {name}")
        except ValueError as exc:
            failures.append(str(exc))
            print(f"  INVALID {name}")

    # The validator itself must reject the shapes Earth Engine rejects.
    print("\nChecking the validator rejects invalid shapes:")
    must_reject = [
        ("_leading_underscore", "this is the bug that shipped"),
        ("9leading_digit", "band names cannot start with a digit"),
        ("has-a-hyphen", "hyphens are not allowed"),
        ("has space", "spaces are not allowed"),
        ("", "empty name"),
        ("has.a.dot", "dots are not allowed"),
    ]
    for bad, why in must_reject:
        try:
            check_band_name(bad)
            failures.append(f"validator accepted {bad!r} ({why})")
            print(f"  FAILED  accepted {bad!r}")
        except ValueError:
            print(f"  ok      rejected {bad!r}")

    # And accepts valid ones.
    for good in ("currently_forest", "combined_eligible", "a", "b2", "A_1"):
        try:
            check_band_name(good)
        except ValueError:
            failures.append(f"validator rejected valid name {good!r}")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print(f"\nAll {len(names)} band names valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

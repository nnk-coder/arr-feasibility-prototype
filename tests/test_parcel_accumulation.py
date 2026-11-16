"""Parcels must not multiply across Streamlit reruns.

Streamlit re-executes the whole script on every interaction — a map pan, a
slider nudge — and st.file_uploader hands back the same files each time. The
app therefore deduplicates on (source, name) before appending to session
state.

That key only holds if nothing downstream renames a stored parcel. An earlier
version disambiguated duplicate names by mutating the parcel in place, so the
next rerun no longer recognised it and appended it again: parcels grew without
bound and the reported project area inflated on every pan, with no error.
"""

import json
import sys

sys.path.insert(0, ".")

from app.geometry import (  # noqa: E402
    build_boundary_from_parcels,
    parcels_from_drawings,
    parcels_from_file,
)

RERUNS = 8


def geojson(name: str, lon: float) -> bytes:
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": name},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [lon, 25.0],
                                [lon + 0.0221, 25.0],
                                [lon + 0.0221, 25.0202],
                                [lon, 25.0202],
                                [lon, 25.0],
                            ]
                        ],
                    },
                }
            ],
        }
    ).encode()


DRAWING = {
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [78.0, 24.0],
                [78.0221, 24.0],
                [78.0221, 24.0202],
                [78.0, 24.0202],
                [78.0, 24.0],
            ]
        ],
    }
}


def simulate(files, with_drawing: bool) -> tuple[list[int], list[float]]:
    """Run the app's accumulate-and-build loop repeatedly, as reruns would."""
    session: list = []
    counts, areas = [], []

    for _ in range(RERUNS):
        # --- the upload branch
        already = {(p.source, p.name) for p in session}
        for fname, data in files:
            for parcel in parcels_from_file(fname, data):
                if (parcel.source, parcel.name) not in already:
                    session.append(parcel)
                    already.add((parcel.source, parcel.name))

        # --- the drawn branch, which dedups on geometry rather than name
        if with_drawing:
            seen_geoms = {
                json.dumps(p.geojson, sort_keys=True) for p in session
            }
            existing_drawn = sum(1 for p in session if p.source == "drawn")
            for parcel in parcels_from_drawings(
                [DRAWING], start_index=existing_drawn + 1
            ):
                sig = json.dumps(parcel.geojson, sort_keys=True)
                if sig not in seen_geoms:
                    session.append(parcel)
                    seen_geoms.add(sig)

        boundary = build_boundary_from_parcels(session)
        counts.append(len(session))
        areas.append(round(boundary.area_ha, 1))

    return counts, areas


def main() -> int:
    failures = []

    cases = [
        (
            "distinct names",
            [("a.geojson", geojson("North", 79.0)), ("b.geojson", geojson("South", 77.0))],
            False,
        ),
        (
            "COLLIDING names across files",
            [("a.geojson", geojson("Block A", 79.0)), ("b.geojson", geojson("Block A", 77.0))],
            False,
        ),
        (
            "files plus a drawn shape",
            [("a.geojson", geojson("Block A", 79.0))],
            True,
        ),
        (
            "colliding names plus a drawn shape",
            [("a.geojson", geojson("Plot", 79.0)), ("b.geojson", geojson("Plot", 77.0))],
            True,
        ),
    ]

    for label, files, drawing in cases:
        counts, areas = simulate(files, drawing)

        stable_count = len(set(counts)) == 1
        stable_area = len(set(areas)) == 1

        if not stable_count:
            failures.append(
                f"{label}: parcel count grew across reruns -> {counts}"
            )
        if not stable_area:
            failures.append(
                f"{label}: reported area drifted across reruns -> {areas}"
            )

        mark = "ok  " if (stable_count and stable_area) else "FAIL"
        print(
            f"  {mark} {label:38} parcels={counts[0]} "
            f"area={areas[0]:,.1f} ha  (stable over {RERUNS} reruns)"
        )

    # Names must still be disambiguated for display, even though the stored
    # parcels are left alone.
    parcels = parcels_from_file("a.geojson", geojson("Block A", 79.0))
    parcels += parcels_from_file("b.geojson", geojson("Block A", 77.0))
    stored_names_before = [p.name for p in parcels]
    boundary = build_boundary_from_parcels(parcels)
    display_names = [p.name for p in boundary.parcels]
    stored_names_after = [p.name for p in parcels]

    print()
    print(f"  stored names before build : {stored_names_before}")
    print(f"  display names from build  : {display_names}")
    print(f"  stored names after build  : {stored_names_after}")

    if stored_names_after != stored_names_before:
        failures.append(
            "build_boundary_from_parcels mutated the stored parcel names; "
            "this is exactly what breaks rerun deduplication"
        )
    if len(set(display_names)) != len(display_names):
        failures.append(f"display names not unique: {display_names}")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print("\nParcel sets are stable across reruns; names disambiguate without mutation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

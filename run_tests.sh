#!/usr/bin/env bash
# Offline checks. None of these need Earth Engine credentials.
#
# The first group needs nothing installed at all — plain Python and the
# standard library — so it runs on a fresh checkout. The second group needs
# the packages from requirements.txt and is skipped with a clear message when
# they are missing, rather than failing with import tracebacks.
set -u

cd "$(dirname "$0")"
failed=0
skipped=0

run() {
    echo "── $1"
    if python3 -I "$2" | tail -1; then :; else failed=1; fi
    echo
}

echo "=== No dependencies required ==="
echo
run "Classification logic"    tests/test_classification_logic.py
run "Credential validation"   tests/test_credential_validation.py
run "Band names"              tests/test_band_names.py
run "Parcel grouping key"     tests/test_parcel_grouping.py

# --- dependency preflight ---------------------------------------------------
missing=$(python3 -I - <<'PY'
needed = ["shapely", "pyproj", "ee", "pandas", "streamlit", "folium"]
missing = []
for name in needed:
    try:
        __import__(name)
    except ImportError:
        missing.append(name)
print(" ".join(missing))
PY
)

echo "=== Requires installed packages ==="
echo
if [ -n "$missing" ]; then
    echo "── SKIPPED - packages not installed:$missing"
    echo
    echo "   These checks exercise geometry and the app itself, so they need"
    echo "   the project's dependencies. Install them with:"
    echo
    echo "       python3 -m venv venv"
    echo "       source venv/bin/activate      # Windows: venv\\Scripts\\activate"
    echo "       pip install -r requirements.txt"
    echo
    echo "   Then run this script again."
    echo
    skipped=1
else
    run "Parcel accumulation"     tests/test_parcel_accumulation.py

    echo "── Geometry pipeline"
    python3 -I -c "
import sys; sys.path.insert(0,'.')
from app.geometry import parcels_from_file, build_boundary_from_parcels
b = build_boundary_from_parcels(parcels_from_file('s.kml', open('samples/sample_bundelkhand.kml','rb').read()))
assert 498 < b.area_ha < 500, f'area drifted: {b.area_ha}'
m = build_boundary_from_parcels(parcels_from_file('m.kml', open('samples/sample_multi_parcel.kml','rb').read()))
assert m.part_count == 3, f'expected 3 parcels, got {m.part_count}'
print(f'single KML {b.area_ha:.1f} ha; multi KML {m.part_count} parcels, {m.area_ha:,.1f} ha')
" || failed=1
    echo
fi

echo "── Compile"
python3 -m py_compile streamlit_app.py app/*.py && echo "all modules compile" || failed=1
echo

if [ "$failed" -ne 0 ]; then
    echo "FAIL - see above"
elif [ "$skipped" -ne 0 ]; then
    echo "PASS (partial) - dependency-free checks green; install packages for the rest"
else
    echo "PASS - all offline checks green"
fi
exit "$failed"

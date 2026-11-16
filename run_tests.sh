#!/usr/bin/env bash
# Run every offline check. None of these need Earth Engine credentials.
set -u

cd "$(dirname "$0")"
failed=0

run() {
    echo "── $1"
    if python3 -I "$2" | tail -1; then :; else failed=1; fi
    echo
}

run "Classification logic"    tests/test_classification_logic.py
run "Credential validation"   tests/test_credential_validation.py
run "Band names"              tests/test_band_names.py

echo "── Geometry pipeline"
python3 -I -c "
import sys; sys.path.insert(0,'.')
from app.geometry import load_boundary
b = load_boundary('s.kml', open('samples/sample_bundelkhand.kml','rb').read())
assert 498 < b.area_ha < 500, f'area drifted: {b.area_ha}'
print(f'sample KML parses to {b.area_ha:.1f} ha')
" || failed=1
echo

echo "── Compile"
python3 -m py_compile streamlit_app.py app/*.py && echo "all modules compile" || failed=1
echo

if [ "$failed" -eq 0 ]; then
    echo "PASS - all offline checks green"
else
    echo "FAIL - see above"
fi
exit "$failed"

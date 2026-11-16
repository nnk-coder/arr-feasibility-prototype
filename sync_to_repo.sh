#!/usr/bin/env bash
# Copy this release into a git repository, replacing managed directories
# wholesale rather than merging into them.
#
# Merging is what caused a half-updated deployment: dragging folders together
# in Finder leaves stale files behind, so streamlit_app.py gets replaced while
# app/ does not, and the app fails on import with no obvious cause.
#
# Usage:  ./sync_to_repo.sh /path/to/arr-feasibility-prototype

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-}"

if [ -z "$DEST" ]; then
    echo "Usage: ./sync_to_repo.sh /path/to/your/repo"
    exit 1
fi
if [ ! -d "$DEST" ]; then
    echo "Not a directory: $DEST"
    exit 1
fi
if [ ! -d "$DEST/.git" ]; then
    echo "Warning: $DEST is not a git repository."
    read -r -p "Continue anyway? [y/N] " reply
    [ "$reply" = "y" ] || exit 1
fi

echo "Syncing from $SRC"
echo "            to $DEST"
echo

# Directories replaced entirely, so a removed file is really removed.
for dir in app tests samples; do
    rm -rf "${DEST:?}/$dir"
    cp -R "$SRC/$dir" "$DEST/$dir"
    # Compiled caches are machine-specific and must never reach a repo.
    find "$DEST/$dir" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    find "$DEST/$dir" -name "*.pyc" -delete 2>/dev/null || true
    echo "  replaced  $dir/"
done

for file in streamlit_app.py requirements.txt README.md run_tests.sh .gitignore .env.example; do
    cp "$SRC/$file" "$DEST/$file"
    echo "  copied    $file"
done

chmod +x "$DEST/run_tests.sh"

# Never carry a local .env into a repository.
if [ -f "$DEST/.env" ]; then
    echo
    echo "  note: $DEST/.env exists locally. It is gitignored and will not"
    echo "        be pushed, but Streamlit secrets take precedence over it."
fi

echo
echo "Done. Next:"
echo "    cd $DEST"
echo "    ./run_tests.sh          # expect PASS"
echo "    git add -A"
echo "    git commit -m 'ARR feasibility v1.0 - all phases'"
echo "    git push origin main"
echo "    git show --stat HEAD | grep app/   # confirm app/ is in the commit"

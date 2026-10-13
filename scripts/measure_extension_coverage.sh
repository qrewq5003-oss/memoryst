#!/data/data/com.termux/files/usr/bin/bash
# True line coverage of one extension module by one test file.
#
#     scripts/measure_extension_coverage.sh test_st_settings_ui.mjs settings-ui.mjs
#     scripts/measure_extension_coverage.sh test_st_fetch_timeout.mjs http.mjs HEAD   # baseline
#
# Why not `node --test --experimental-test-coverage` on the tree as it is: the modules
# import each other as `./x.mjs?v=<build>` (see scripts/stamp_extension_build.py) and the
# tests import them bare. node treats the two URLs as two modules, and its report shows
# ONE row per path - the stamped copy whenever a sibling pulled it in. So the row for
# http.mjs under test_st_fetch_timeout.mjs described the copy trackers.mjs loaded, not the
# one the test exercised: 90% shown, 97% real, and after adding functions the shown number
# fell while the real one rose. Measuring "by one test file" is not enough; the stamps
# have to go. This copies the tree into a scratch directory, strips them there, and runs.
#
# The optional third argument is a git revision to measure instead of the working tree.
set -euo pipefail
test_file="${1:?test file, e.g. test_st_settings_ui.mjs}"
module="${2:?module, e.g. settings-ui.mjs}"
rev="${3:-}"
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
if [ -n "$rev" ]; then
    git -C "$root" archive "$rev" tests sillytavern-extension | tar -x -C "$work"
else
    cp -r "$root/tests" "$root/sillytavern-extension" "$work/"
fi
sed -i -E 's/\?v=[0-9a-f]+//g' "$work"/sillytavern-extension/*.mjs
cd "$work"
node --test --experimental-test-coverage "tests/$test_file" 2>&1 \
    | grep -E "^ℹ +(file|$module) " | sed -E 's/ +/ /g'

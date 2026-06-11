#!/usr/bin/env bash
# Package the comp-analysis skill into a ZIP for upload to Claude Desktop.
#
# Why this exists: Desktop installs a *static copy* of the uploaded zip (server-registered),
# so unlike the Claude Code symlink (~/.claude/skills), repo edits don't auto-propagate to
# Desktop. Re-run this and re-upload whenever you want Desktop on the latest skill version.
#
# Usage:
#   ./scripts/package-skill.sh            # -> dist/comp-analysis.zip
#   ./scripts/package-skill.sh ~/Claude   # write the zip into ~/Claude (Desktop's sandbox dir)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo_root/skill/comp-analysis"
out_dir="${1:-$repo_root/dist}"

[ -f "$src/SKILL.md" ] || { echo "error: $src/SKILL.md not found" >&2; exit 1; }
mkdir -p "$out_dir"
out="$out_dir/comp-analysis.zip"

# Zip with a top-level "comp-analysis/" folder. Python's zipfile avoids a hard `zip` dependency
# and stays deterministic. (If Desktop ever wants SKILL.md at the archive root instead, change
# the arcname base below from the skill's parent to "$src".)
SRC="$src" OUT="$out" python3 - <<'PY'
import os, zipfile
src, out = os.environ["SRC"], os.environ["OUT"]
base = os.path.dirname(src)  # so arcnames start with "comp-analysis/"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            z.write(full, os.path.relpath(full, base))
PY

echo "Built $out"
python3 -c "import zipfile,sys; [print('  '+n) for n in zipfile.ZipFile(sys.argv[1]).namelist()]" "$out"

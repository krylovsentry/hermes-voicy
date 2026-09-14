#!/usr/bin/env bash
# Idempotent installer for the hermes-voicy plugin.
#
#   1. Symlinks $HERMES_HOME/plugins/hermes-voicy -> this repo (ADR-0004).
#   2. Ensures 'hermes-voicy' is in plugins.enabled in config.yaml.
#
# Re-run after moving the repo. Uninstall: remove the symlink and drop the
# entry from plugins.enabled.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
LINK="$PLUGINS_DIR/hermes-voicy"
CONFIG="$HERMES_HOME/config.yaml"

mkdir -p "$PLUGINS_DIR"

# 1. Symlink (replace whatever is there if it's our link or stale)
if [ -L "$LINK" ]; then
  current="$(readlink "$LINK")"
  if [ "$current" != "$REPO" ]; then
    echo "Updating symlink: $current -> $REPO"
    rm "$LINK"
    ln -s "$REPO" "$LINK"
  fi
elif [ -e "$LINK" ]; then
  echo "ERROR: $LINK exists and is not a symlink. Move it aside first." >&2
  exit 1
else
  ln -s "$REPO" "$LINK"
  echo "Created symlink: $LINK -> $REPO"
fi

# 2. plugins.enabled entry (YAML edit via python, preserves file)
python3 - "$CONFIG" <<'PY'
import re
import sys

path = sys.argv[1]
try:
    text = open(path, encoding="utf-8").read()
except FileNotFoundError:
    print(f"  config.yaml not found at {path} — skipping enable step")
    sys.exit(0)

if re.search(r"^\s*-\s*hermes-voicy\s*$", text, re.M):
    print("  plugins.enabled already has hermes-voicy")
    sys.exit(0)

# Case A: block-form list
#     plugins:
#       enabled:
#         - foo
m = re.search(r"^(plugins:[ \t]*\n(?:[^\n]*\n)*?[ \t]*enabled:[ \t]*\n(?:[ \t]+-[^\n]+\n)*)", text, re.M)
if m:
    text = text[: m.end()] + "    - hermes-voicy\n" + text[m.end():]
    print("  Appended 'hermes-voicy' to plugins.enabled")
    open(path, "w", encoding="utf-8").write(text)
    sys.exit(0)

# Case B: inline empty list
m = re.search(r"^([ \t]*)enabled:[ \t]*\[[ \t]*\][ \t]*\n", text, re.M)
if m and re.search(r"^plugins:", text, re.M):
    text = text[: m.start()] + m.group(1) + "enabled:\n" + m.group(1) + "  - hermes-voicy\n" + text[m.end():]
    print("  Converted inline plugins.enabled: [] to a list")
    open(path, "w", encoding="utf-8").write(text)
    sys.exit(0)

# Case C: no enabled: key at all -> insert under plugins:
m2 = re.search(r"^(plugins:[ \t]*\n)", text, re.M)
if m2:
    text = text[: m2.end()] + "  enabled:\n    - hermes-voicy\n" + text[m2.end():]
    print("  Created plugins.enabled with hermes-voicy")
else:
    text += "\nplugins:\n  enabled:\n    - hermes-voicy\n"
    print("  Appended plugins.enabled block")

open(path, "w", encoding="utf-8").write(text)
PY

echo
echo "hermes-voicy installed. Verify with:"
echo "  hermes plugins list | grep voicy"
echo "  /voicy test all     (inside a hermes session)"

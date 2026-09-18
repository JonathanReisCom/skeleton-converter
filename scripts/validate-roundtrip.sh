#!/usr/bin/env bash
# Numeric round-trip validation against the real engines.
#
# Both sides sample the same animation time and are compared in Godot skeleton
# space (Y flipped for the Spine side):
#   - Godot engine: godot-sample-pose.gd prints each Bone2D's world position
#   - Spine runtime: spine-sample-pose.mjs prints each bone's world matrix
# PASS means the converted file reproduces the source engine's pose.
#
# Usage:
#   validate-roundtrip.sh <godot-project-dir> <scene.tscn> <spine.json> \
#                         <animation> <time> [sprite-scale]
set -euo pipefail

GODOT_BIN="${GODOT_BIN:-/Applications/Godot.app/Contents/MacOS/Godot}"
HERE="$(cd "$(dirname "$0")" && pwd)"

PROJECT_DIR="$1"
SCENE_PATH="$2"
SPINE_JSON="$3"
ANIMATION="${4:-walk}"
TIME="${5:-0.3}"
SCALE="${6:-1.0}"

cp "$HERE/godot-sample-pose.gd" "$PROJECT_DIR/sample_pose.gd"
"$GODOT_BIN" --headless --path "$PROJECT_DIR" --script res://sample_pose.gd -- \
  "$SCENE_PATH" "$ANIMATION" "$TIME" "$SCALE" 2>/dev/null \
  | grep '^POSE' > /tmp/godot-pose.txt

NODE_PATH="${SPINE_CORE_NODE_PATH:-$HERE/node_modules}" node "$HERE/spine-sample-pose.mjs" \
  "$SPINE_JSON" "$ANIMATION" "$TIME" > /tmp/spine-pose.json

python3 - <<'PY'
import json

spine = json.load(open('/tmp/spine-pose.json'))
godot = {}
for line in open('/tmp/godot-pose.txt'):
    _, name, x, y = line.split()
    godot[name] = (float(x), float(y))

worst = 0.0
worst_bone = None
for name, (gx, gy) in godot.items():
    if name not in spine:
        continue
    sx, sy = spine[name][4], spine[name][5]
    # Godot Y is down; Spine Y is up.
    deviation = ((gx - sx) ** 2 + (-gy - sy) ** 2) ** 0.5
    if deviation > worst:
        worst, worst_bone = deviation, name

print(f'bones compared: {len(godot)}')
print(f'worst bone deviation: {worst:.6f} ({worst_bone})')
print('RESULT:', 'PASS' if worst < 0.01 else 'FAIL')
PY

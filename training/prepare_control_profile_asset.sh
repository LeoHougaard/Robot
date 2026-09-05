#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROFILE="${1:-}"
readonly ASSET_ROOT="/workspace/projects/assets/onshape"
readonly CONVERTER="/workspace/projects/training/convert_onshape_gltf_to_usd.py"
readonly LOG_ROOT="/workspace/projects/training/diagnostics"

[[ "$PROFILE" == /workspace/projects/training/control_profiles/*.json ]] || {
  printf 'Control profile is outside the training control_profiles directory: %s\n' "$PROFILE" >&2
  exit 2
}
[[ -f "$PROFILE" ]] || {
  printf 'Control profile does not exist: %s\n' "$PROFILE" >&2
  exit 2
}

mapfile -t profile_values < <(
  /workspace/isaaclab/_isaac_sim/kit/python/bin/python3 - "$PROFILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    robot = json.load(handle)["robot"]
print(robot["asset_source"])
print(robot["asset_usd"])
PY
)
asset_source="${profile_values[0]:-}"
asset="${profile_values[1]:-}"

if [[ "$asset_source" == "Isaac Lab built-in" ]]; then
  printf 'Built-in Isaac Lab asset needs no mesh preparation.\n'
  exit 0
fi
[[ "$asset_source" == "Workspace USD" ]] || {
  printf 'Unsupported robot asset source: %s\n' "$asset_source" >&2
  exit 2
}
# Native collision overlays already reference prepared USD meshes. Verify the
# conversion evidence and every source layer instead of treating them as a
# Publisher glTF export (which would overwrite the reviewed overlay).
if [[ "$asset" == "$ASSET_ROOT"/*/robot.usda && -f "${asset%.usda}.manifest.json" ]]; then
  /workspace/isaaclab/_isaac_sim/kit/python/bin/python3 - "$asset" "$ASSET_ROOT" <<'PY'
import hashlib, json, sys
from pathlib import Path
asset, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
assert asset.parent.parent == root and asset.name == "robot.usda"
manifest = json.loads(asset.with_suffix(".manifest.json").read_text())
assert manifest["output"] == str(asset)
assert manifest["unchanged_attribute_check"] is True
assert len(set(manifest["changed_collision_roots"])) == 29
assert hashlib.sha256(asset.read_bytes()).hexdigest() == manifest["output_sha256"]
assert manifest["source_layers"]
for name, expected in manifest["source_layers"].items():
    layer = Path(name).resolve()
    assert layer.is_relative_to(root)
    assert hashlib.sha256(layer.read_bytes()).hexdigest() == expected, f"changed source layer: {layer}"
print("Verified native collision overlay and all referenced source layers:", asset)
PY
  exit 0
fi
[[ "$asset" == "$ASSET_ROOT"/*/robot.usd ]] || {
  printf 'Custom training asset must be one robot.usd directly below %s: %s\n' "$ASSET_ROOT" "$asset" >&2
  exit 2
}

asset_dir="$(realpath -m "$(dirname "$asset")")"
[[ "$asset" == "$asset_dir/robot.usd" && "$asset_dir" == "$ASSET_ROOT"/* ]] || {
  printf 'Custom training asset did not resolve below the dedicated Onshape root: %s\n' "$asset" >&2
  exit 2
}
source_asset="$asset_dir/robot.usda"
output_dir="$asset_dir/usd_meshes"
[[ -f "$source_asset" ]] || {
  printf 'Publisher source asset is missing below %s.\n' "$asset_dir" >&2
  exit 1
}

mapfile -t referenced_meshes < <(
  sed -n 's#.*@\./\([^@]*\.gltf\)@.*#\1#p' "$source_asset" | sort -u
)
(( ${#referenced_meshes[@]} > 0 )) || {
  printf 'Publisher source asset contains no glTF mesh references: %s\n' "$source_asset" >&2
  exit 1
}
for mesh in "${referenced_meshes[@]}"; do
  source_mesh="$(realpath -m "$asset_dir/$mesh")"
  [[ "$source_mesh" == "$asset_dir/"* && "$mesh" != /* && -s "$source_mesh" ]] || {
    printf 'Referenced Publisher mesh is missing or unsafe: %s\n' "$mesh" >&2
    exit 1
  }
done

needs_conversion=0
for mesh in "${referenced_meshes[@]}"; do
  output="$output_dir/${mesh%.gltf}.usd"
  if [[ ! -s "$output" || "$asset_dir/$mesh" -nt "$output" ]]; then
    needs_conversion=1
  fi
done

if (( needs_conversion )); then
  mkdir -p "$output_dir" "$LOG_ROOT"
  log="$LOG_ROOT/$(basename "$asset_dir")-gltf-conversion.log"
  printf 'Converting %d Publisher glTF meshes for %s.\n' \
    "${#referenced_meshes[@]}" "$(basename "$asset_dir")"
  : >"$log"
  declare -A seen_source_dirs=()
  source_dirs=()
  for mesh in "${referenced_meshes[@]}"; do
    source_rel="$(dirname -- "$mesh")"
    if [[ -z "${seen_source_dirs[$source_rel]+present}" ]]; then
      seen_source_dirs[$source_rel]=1
      source_dirs+=("$source_rel")
    fi
  done
  for source_rel in "${source_dirs[@]}"; do
    mkdir -p "$output_dir/$source_rel"
    /workspace/isaaclab/isaaclab.sh -p "$CONVERTER" \
      --input-dir "$asset_dir/$source_rel" \
      --output-dir "$output_dir/$source_rel" \
      --viz=none 2>&1 | tee -a "$log"
  done
fi

for mesh in "${referenced_meshes[@]}"; do
  [[ -s "$output_dir/${mesh%.gltf}.usd" ]] || {
    printf 'Native USD mesh is missing: %s\n' "$output_dir/${mesh%.gltf}.usd" >&2
    exit 1
  }
done

temporary="$asset_dir/.robot.usd.$$"
trap 'rm -f -- "$temporary"' EXIT
sed 's#@\./\([^@]*\)\.gltf@#@./usd_meshes/\1.usd@#g' \
  "$source_asset" >"$temporary"
sed -i \
  's/prepend apiSchemas = \["IsaacRobotAPI"\]/prepend apiSchemas = ["IsaacRobotAPI", "PhysicsArticulationRootAPI"]/' \
  "$temporary"
if grep -q '@\./.*\.gltf@' "$temporary"; then
  printf 'Derived training layer still contains glTF references.\n' >&2
  exit 1
fi
if ! grep -q 'PhysicsArticulationRootAPI' "$temporary"; then
  printf 'Derived training layer is missing PhysicsArticulationRootAPI.\n' >&2
  exit 1
fi
mv -f -- "$temporary" "$asset"
trap - EXIT
printf 'Control-profile asset ready: %s (%d native meshes).\n' \
  "$asset" "${#referenced_meshes[@]}"

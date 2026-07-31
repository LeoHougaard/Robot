#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_DIR="/workspace/projects/assets/onshape/simple-8-joint-dog/meshes"
readonly OUTPUT_DIR="/workspace/projects/assets/onshape/simple-8-joint-dog/usd_meshes"
readonly CONVERTER="/workspace/projects/training/convert_onshape_gltf_to_usd.py"
readonly LOG="/workspace/projects/training/diagnostics/gltf-conversion.log"
readonly EXPECTED_IDS=(
  "_M0BhnApLFcIwi9FiI"
  "_M0GXc29ZQLKLZRigV"
  "_MDzoh4cCiQOb3LDc6"
  "_MFFQ2p25_Tl8Qbl6J"
  "_MPiebr7IdhajYW6eO"
  "_MdQLAKl_Scf81bKbb"
  "_MkA4XIXvGXiT_qjpE"
  "_Ml_IVwTR18YxKzP3h"
  "_MqjGc65gNtWvXfvxi"
)

if [[ ! -d "$SOURCE_DIR" ]]; then
  printf 'Publisher mesh directory is missing: %s\n' "$SOURCE_DIR" >&2
  exit 1
fi

shopt -s nullglob
source_meshes=("$SOURCE_DIR"/*.gltf)
if (( ${#source_meshes[@]} != ${#EXPECTED_IDS[@]} )); then
  printf 'Expected %d Publisher meshes, found %d. Refusing to train a mismatched asset.\n' \
    "${#EXPECTED_IDS[@]}" "${#source_meshes[@]}" >&2
  exit 1
fi

needs_conversion=0
for id in "${EXPECTED_IDS[@]}"; do
  if [[ ! -f "$SOURCE_DIR/$id.gltf" ]]; then
    printf 'Expected stable Publisher mesh ID is missing: %s.gltf\n' "$id" >&2
    printf 'The Onshape topology changed; regenerate the authored training layer before training.\n' >&2
    exit 1
  fi
  if [[ ! -f "$OUTPUT_DIR/$id.usd" ]]; then
    needs_conversion=1
  fi
done

if (( needs_conversion == 0 )); then
  printf 'Native USD mesh preflight passed (%d of %d).\n' \
    "${#EXPECTED_IDS[@]}" "${#EXPECTED_IDS[@]}"
  exit 0
fi

mkdir -p "$OUTPUT_DIR" "$(dirname "$LOG")"
printf 'Native USD meshes are incomplete; converting Publisher glTF geometry.\n'
/workspace/isaaclab/isaaclab.sh -p "$CONVERTER" \
  --input-dir "$SOURCE_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --viz=none 2>&1 | tee "$LOG"

for id in "${EXPECTED_IDS[@]}"; do
  if [[ ! -s "$OUTPUT_DIR/$id.usd" ]]; then
    printf 'Native conversion output is missing or empty: %s.usd\n' "$id" >&2
    exit 1
  fi
done
printf 'Native USD mesh preflight passed after conversion (%d of %d).\n' \
  "${#EXPECTED_IDS[@]}" "${#EXPECTED_IDS[@]}"

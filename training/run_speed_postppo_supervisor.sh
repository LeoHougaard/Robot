#!/usr/bin/env bash
set -Eeuo pipefail

RUN_ID="20260906T170329Z-train-51934"
WAIT_PID="${1:?exact PPO PID required}"
PROJECT="/home/leo/isaac-workspace/projects"
TRAINING="${PROJECT}/training"
REVIEW="${TRAINING}/reviews/20260906-speed/unique-run"
RUN="${TRAINING}/runs/simple_dog/${RUN_ID}"
SOURCE="${RUN}/source"
LOG="${REVIEW}/supervisor.log"
BASELINE="${TRAINING}/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_10-33-19/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_2750_rew_336.68628.pth"

mkdir -p "${REVIEW}"
exec > >(tee -a "${LOG}") 2>&1
printf 'supervisor_start=%s\nrun_id=%s\nwait_pid=%s\n' "$(date -Is)" "${RUN_ID}" "${WAIT_PID}"
test -f "${SOURCE}/evaluate_delivery_stride.py"
test -f "${BASELINE}"
test ! -e "${REVIEW}/baseline-slow20.json"

while kill -0 "${WAIT_PID}" 2>/dev/null; do sleep 10; done
while pgrep -f "${RUN}/source/train_simple_dog.py" >/dev/null 2>&1; do sleep 10; done

if pgrep -af 'train_simple_dog.py|evaluate_delivery_stride.py|train_delivery_stride.py' >/dev/null 2>&1; then
  echo 'refusing evaluation: another training/evaluation process is active' >&2
  exit 20
fi

CANDIDATE="$(find "${TRAINING}/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_17-03-44/nn" -maxdepth 1 -type f -name '*ep_3250_*.pth' -print | sort | tail -1)"
test -n "${CANDIDATE}"
sha256sum "${BASELINE}" "${CANDIDATE}" "${SOURCE}/evaluate_delivery_stride.py" \
  "${RUN}/source_manifest.json" "${RUN}/control_profile.json" > "${REVIEW}/identity.sha256"
printf 'baseline=%s\ncandidate=%s\nevaluator=%s\n' "${BASELINE}" "${CANDIDATE}" "${SOURCE}/evaluate_delivery_stride.py" > "${REVIEW}/identity.txt"

run_eval() {
  local label="$1" checkpoint="$2" stage="$3" envs="$4" extra="$5"
  test ! -e "${REVIEW}/${label}.json"
  echo "starting ${label}"
  docker exec isaac-lab-gb10 bash -lc \
    "cd /workspace/projects/training && /workspace/isaaclab/_isaac_sim/python.sh -c 'from isaaclab.cli import cli; cli()' -p '${SOURCE}/evaluate_delivery_stride.py' --checkpoint '${checkpoint}' --family v22 --output '/workspace/projects/training/reviews/20260906-speed/unique-run/${label}.json' --commands --stage '${stage}' --num-envs '${envs}' --seconds 20 ${extra} --headless --device=cuda:0" \
    2>&1 | tee "${REVIEW}/${label}.log"
}

run_eval baseline-slow20 "${BASELINE}" commands 8 ""
run_eval candidate-slow20 "${CANDIDATE}" commands 8 ""
run_eval baseline-speed20 "${BASELINE}" speed 14 ""
run_eval candidate-speed20 "${CANDIDATE}" speed 14 ""

for pair in "candidate-fast-forward60 8" "candidate-fast-turn60 12"; do
  label="${pair% *}"; index="${pair##* }"
  test ! -e "${REVIEW}/${label}.json"
  docker exec isaac-lab-gb10 bash -lc \
    "cd /workspace/projects/training && /workspace/isaaclab/_isaac_sim/python.sh -c 'from isaaclab.cli import cli; cli()' -p '${SOURCE}/evaluate_delivery_stride.py' --checkpoint '${CANDIDATE}' --family v22 --output '/workspace/projects/training/reviews/20260906-speed/unique-run/${label}.json' --commands --stage speed --command-index '${index}' --num-envs 1 --seconds 60 --video-folder '/workspace/projects/training/reviews/20260906-speed/unique-run/${label}-video' --headless --device=cuda:0" \
    2>&1 | tee "${REVIEW}/${label}.log"
done

date -Is > "${REVIEW}/completed"
echo 'supervisor_complete'

"""CurrentBodyV7 environment with distance holds and physical body pushes."""

import math

import torch

from simple_dog_task_current_body_v5.simple_dog_current_body_v5_env import SimpleDogCurrentBodyV5Env

from .simple_dog_current_body_v7_env_cfg import SimpleDogCurrentBodyV7HardEnvCfg


class SimpleDogCurrentBodyV7Env(SimpleDogCurrentBodyV5Env):
    cfg: SimpleDogCurrentBodyV7HardEnvCfg

    def __init__(
        self,
        cfg: SimpleDogCurrentBodyV7HardEnvCfg,
        render_mode=None,
        **kwargs,
    ):
        super().__init__(cfg, render_mode, **kwargs)
        self._v7_push_force_steps_remaining = torch.zeros(
            self.num_envs, dtype=torch.long, device=self.device
        )
        self._v7_push_body_ids = torch.tensor(
            self._base_body_ids, dtype=torch.int32, device=self.device
        )
        # A new V7 environment must never inherit a stale permanent wrench
        # from simulator setup or a previous reset.
        self._robot.permanent_wrench_composer.reset()

    def _apply_random_pushes(self) -> None:
        """Apply short off-centre forces after settling, with quiet walk time."""

        # Clear a completed force before the next physics step.  The wrench
        # composer otherwise intentionally keeps it active at every substep.
        active = self._v7_push_force_steps_remaining > 0
        self._v7_push_force_steps_remaining[active] -= 1
        expired = torch.nonzero(
            active & (self._v7_push_force_steps_remaining <= 0), as_tuple=False
        ).squeeze(-1)
        if len(expired):
            self._robot.permanent_wrench_composer.reset(env_ids=expired)

        difficulty = max(
            self._difficulty_fraction(), self.cfg.push_difficulty_floor
        )
        probability = self.cfg.push_probability * difficulty
        if probability <= 0.0:
            return

        # Never disturb the two-second locked drop.  Active policies also get
        # at least the configured 6-10 second interval between force events.
        settling = self._reset_settle_steps_remaining > 0
        eligible = (~settling) & (self._v7_push_force_steps_remaining <= 0)
        self._push_steps_remaining[eligible] -= 1
        due = torch.nonzero(
            eligible & (self._push_steps_remaining <= 0), as_tuple=False
        ).squeeze(-1)
        if not len(due):
            return
        self._push_steps_remaining[due] = self._random_step_counts(
            len(due), self.cfg.push_interval_s
        )
        selected = due[
            torch.rand(len(due), device=self.device) < probability
        ]
        if not len(selected):
            return

        count = len(selected)
        body_count = len(self._base_body_ids)
        angle = torch.empty(count, device=self.device).uniform_(
            -math.pi, math.pi
        )
        force_low, force_high = self.cfg.push_force_n
        magnitude = torch.empty(count, device=self.device).uniform_(
            force_low, force_high
        ) * difficulty
        forces_b = torch.zeros(
            count, body_count, 3, device=self.device
        )
        forces_b[:, :, 0] = (magnitude * torch.cos(angle)).unsqueeze(1)
        forces_b[:, :, 1] = (magnitude * torch.sin(angle)).unsqueeze(1)

        torques_b = torch.zeros_like(forces_b)
        torques_b[:, :, 2] = torch.empty(
            count, body_count, device=self.device
        ).uniform_(
            -self.cfg.push_yaw_torque_nm * difficulty,
            self.cfg.push_yaw_torque_nm * difficulty,
        )

        offset_limit = torch.tensor(
            self.cfg.push_application_offset_m,
            device=self.device,
            dtype=forces_b.dtype,
        ).view(1, 1, 3)
        positions_b = (
            2.0 * torch.rand_like(forces_b) - 1.0
        ) * offset_limit
        self._robot.permanent_wrench_composer.set_forces_and_torques_index(
            forces=forces_b,
            torques=torques_b,
            positions=positions_b,
            body_ids=self._v7_push_body_ids,
            env_ids=selected,
            is_global=False,
        )

        duration_low, duration_high = self.cfg.push_force_duration_s
        minimum_steps = max(1, round(duration_low / self.step_dt))
        maximum_steps = max(minimum_steps, round(duration_high / self.step_dt))
        self._v7_push_force_steps_remaining[selected] = torch.randint(
            minimum_steps,
            maximum_steps + 1,
            (count,),
            device=self.device,
        )

    def _reset_idx(self, env_ids: torch.Tensor | None):
        resolved_env_ids = (
            torch.arange(self.num_envs, dtype=torch.long, device=self.device)
            if env_ids is None
            else env_ids
        )
        if hasattr(self, "_v7_push_force_steps_remaining"):
            self._robot.permanent_wrench_composer.reset(env_ids=resolved_env_ids)
        super()._reset_idx(env_ids)
        if hasattr(self, "_v7_push_force_steps_remaining"):
            self._v7_push_force_steps_remaining[resolved_env_ids] = 0

"""CurrentBodyV18 with subordinate opposite-leg synchronization."""

import torch

from simple_dog_task_current_body_v17.simple_dog_current_body_v17_env import (
    SimpleDogCurrentBodyV17Env,
)

from .simple_dog_current_body_v18_env_cfg import SimpleDogCurrentBodyV18HardEnvCfg


class SimpleDogCurrentBodyV18Env(SimpleDogCurrentBodyV17Env):
    """Pay opposite-leg synchronization only after useful body progress."""

    cfg: SimpleDogCurrentBodyV18HardEnvCfg

    def __init__(self, cfg, render_mode=None, **kwargs):
        super().__init__(cfg, render_mode, **kwargs)
        self._episode_sums["opposite_leg_sync"] = torch.zeros(
            self.num_envs, device=self.device
        )

    def _get_rewards(self) -> torch.Tensor:
        reward = super()._get_rewards()
        body_forward, body_lateral, _, _, _, _ = self._get_physical_motion()
        yaw_rate = self._semantic_vector_b(
            self._robot.data.root_ang_vel_b.torch
        )[:, 2]
        requested_planar = self._commands[:, :2]
        requested_speed = torch.linalg.vector_norm(requested_planar, dim=1)
        planar_active = requested_speed > 0.05
        planar_direction = requested_planar / requested_speed.clamp_min(
            1.0e-6
        ).unsqueeze(1)
        actual_planar = torch.stack((body_forward, body_lateral), dim=1)
        planar_progress = torch.clamp(
            torch.sum(actual_planar * planar_direction, dim=1)
            / requested_speed.clamp_min(1.0e-6),
            0.0,
            1.0,
        )
        yaw_command = self._commands[:, 2]
        yaw_active = torch.abs(yaw_command) > 0.05
        yaw_progress = torch.clamp(
            yaw_rate * torch.sign(yaw_command)
            / torch.abs(yaw_command).clamp_min(1.0e-6),
            0.0,
            1.0,
        )
        active_count = planar_active.float() + yaw_active.float()
        progress = (
            planar_progress * planar_active.float()
            + yaw_progress * yaw_active.float()
        ) / active_count.clamp_min(1.0)

        current_air_time = self._contact_sensor.data.current_air_time.torch[
            :, self._feet_sensor_ids
        ]
        current_contact_time = (
            self._contact_sensor.data.current_contact_time.torch[
                :, self._feet_sensor_ids
            ]
        )
        synchronization = self._dense_diagonal_gait_reward(
            current_air_time, current_contact_time
        )
        sync_rate = (
            self.cfg.opposite_leg_sync_reward_scale
            * progress
            * synchronization
        )
        settling = getattr(
            self,
            "_reset_hold_active_mask",
            torch.zeros(self.num_envs, dtype=torch.bool, device=self.device),
        )
        sync_reward = torch.where(
            settling, 0.0, sync_rate * self.step_dt
        )
        self._episode_sums["opposite_leg_sync"] += sync_reward
        return reward + sync_reward

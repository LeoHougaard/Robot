"""CurrentBodyV17 locomotion discovery with translation, yaw, and levelness."""

import torch

from simple_dog_task_current_body_v16.simple_dog_current_body_v16_env import (
    SimpleDogCurrentBodyV16Env,
)

from .simple_dog_current_body_v17_env_cfg import SimpleDogCurrentBodyV17HardEnvCfg


class SimpleDogCurrentBodyV17Env(SimpleDogCurrentBodyV16Env):
    """Train only commanded planar motion, yaw motion, and a level body."""

    cfg: SimpleDogCurrentBodyV17HardEnvCfg

    def _sample_command_targets(
        self, env_ids: torch.Tensor, *, immediate: bool
    ) -> None:
        count = len(env_ids)
        targets = torch.zeros(count, 3, device=self.device)
        sample = torch.rand(count, device=self.device)
        forward_end = self.cfg.locomotion_forward_fraction
        lateral_end = forward_end + self.cfg.locomotion_lateral_fraction
        mixed_end = lateral_end + self.cfg.locomotion_mixed_fraction

        forward = sample < forward_end
        lateral = (sample >= forward_end) & (sample < lateral_end)
        mixed = (sample >= lateral_end) & (sample < mixed_end)
        yaw_only = sample >= mixed_end

        forward_count = int(forward.sum().item())
        if forward_count:
            targets[forward, 0] = self._signed_magnitude(
                forward_count, *self.cfg.locomotion_speed_range, self.device
            )
        lateral_count = int(lateral.sum().item())
        if lateral_count:
            targets[lateral, 1] = self._signed_magnitude(
                lateral_count,
                *self.cfg.locomotion_lateral_speed_range,
                self.device,
            )
        mixed_count = int(mixed.sum().item())
        if mixed_count:
            planar_angle = torch.empty(mixed_count, device=self.device).uniform_(
                -torch.pi, torch.pi
            )
            planar_speed = torch.empty(mixed_count, device=self.device).uniform_(
                *self.cfg.locomotion_speed_range
            )
            targets[mixed, 0] = planar_speed * torch.cos(planar_angle)
            targets[mixed, 1] = planar_speed * torch.sin(planar_angle)
            targets[mixed, 2] = self._signed_magnitude(
                mixed_count, *self.cfg.locomotion_yaw_rate_range, self.device
            )
        yaw_count = int(yaw_only.sum().item())
        if yaw_count:
            targets[yaw_only, 2] = self._signed_magnitude(
                yaw_count, *self.cfg.locomotion_yaw_rate_range, self.device
            )

        if hasattr(self, "_v4_command_modes"):
            self._v4_command_modes[env_ids] = 2
        self._command_targets[env_ids] = targets
        hold_steps = self._random_step_counts(count, self.cfg.command_hold_s)
        self._command_steps_remaining[env_ids] = hold_steps
        if immediate:
            self._commands[env_ids] = targets
        if hasattr(self, "_posture_targets"):
            self._sample_posture_targets(env_ids, immediate=immediate)

    def _sample_posture_targets(
        self, env_ids: torch.Tensor, *, immediate: bool
    ) -> None:
        targets = torch.zeros(len(env_ids), 3, device=self.device)
        self._posture_targets[env_ids] = targets
        self._posture_steps_remaining[env_ids] = self._command_steps_remaining[
            env_ids
        ]
        if immediate:
            self._posture_commands[env_ids] = targets

    def _get_rewards(self) -> torch.Tensor:
        body_forward, body_lateral, _, _, _, _ = self._get_physical_motion()
        yaw_rate = self._semantic_vector_b(
            self._robot.data.root_ang_vel_b.torch
        )[:, 2]
        _, roll, pitch = self._body_posture()
        actual_planar = torch.stack((body_forward, body_lateral), dim=1)
        requested_planar = self._commands[:, :2]
        requested_speed = torch.linalg.vector_norm(requested_planar, dim=1)
        planar_active = requested_speed > 0.05
        yaw_command = self._commands[:, 2]
        yaw_active = torch.abs(yaw_command) > 0.05

        planar_direction = requested_planar / requested_speed.clamp_min(
            1.0e-6
        ).unsqueeze(1)
        aligned_speed = torch.sum(actual_planar * planar_direction, dim=1)
        planar_progress = torch.clamp(
            aligned_speed / requested_speed.clamp_min(1.0e-6), -1.0, 1.0
        )
        yaw_progress = torch.clamp(
            yaw_rate * torch.sign(yaw_command)
            / torch.abs(yaw_command).clamp_min(1.0e-6),
            -1.0,
            1.0,
        )
        active_count = planar_active.float() + yaw_active.float()
        progress = (
            planar_progress * planar_active.float()
            + yaw_progress * yaw_active.float()
        ) / active_count.clamp_min(1.0)

        planar_error = torch.linalg.vector_norm(
            actual_planar - requested_planar, dim=1
        )
        yaw_error = torch.abs(yaw_rate - yaw_command)
        tracking_error = (
            planar_error / 0.10 * planar_active.float()
            + yaw_error / 0.30 * yaw_active.float()
        ) / active_count.clamp_min(1.0)
        tracking = torch.exp(-tracking_error)
        shortfall = 1.0 - torch.clamp(progress, 0.0, 1.0)
        level_error = torch.sqrt(torch.square(roll) + torch.square(pitch))
        level_penalty = torch.clamp(
            level_error / self.cfg.locomotion_level_tolerance_rad, 0.0, 2.0
        )

        reward_rate = (
            self.cfg.locomotion_tracking_reward_scale * tracking
            + self.cfg.locomotion_progress_reward_scale * progress
            + self.cfg.locomotion_shortfall_penalty_scale * shortfall
            + self.cfg.locomotion_level_penalty_scale * level_penalty
        )
        settling = getattr(
            self,
            "_reset_hold_active_mask",
            torch.zeros(self.num_envs, dtype=torch.bool, device=self.device),
        )
        active = (~settling).to(body_forward.dtype)
        self._survival_steps += 1.0
        self._velocity_error_sum += active * planar_error
        self._world_forward_speed_sum += active * body_forward
        self._body_lateral_speed_sum += active * torch.abs(body_lateral)
        self._heading_error_sum += active * yaw_error
        tracked_speed = torch.maximum(
            torch.minimum(aligned_speed, requested_speed), -requested_speed
        )
        self._terrain_commanded_distance += (
            active * requested_speed * self.step_dt
        )
        self._terrain_tracked_distance += active * tracked_speed * self.step_dt

        reward = reward_rate * self.step_dt
        reward = torch.where(settling, 0.0, reward)
        self._episode_sums["body_tracking"] += torch.where(
            settling,
            0.0,
            (
                self.cfg.locomotion_tracking_reward_scale * tracking
                + self.cfg.locomotion_progress_reward_scale * progress
                + self.cfg.locomotion_level_penalty_scale * level_penalty
            )
            * self.step_dt,
        )
        self._episode_sums["body_motion_shortfall"] += torch.where(
            settling,
            0.0,
            self.cfg.locomotion_shortfall_penalty_scale
            * shortfall
            * self.step_dt,
        )
        if self.cfg.print_play_metrics and not self._evaluation_segments and not bool(settling[0].item()):
            self._play_step_count += 1
        return reward

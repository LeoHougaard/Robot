"""Current-aware rough locomotion with deployable posture commands."""

from __future__ import annotations

import math

import torch

from simple_dog_task_v2.simple_dog_v2_env import SimpleDogV2Env
from .simple_dog_current_env_cfg import SimpleDogCurrentV3RoughEnvCfg


class SimpleDogCurrentV3Env(SimpleDogV2Env):
    cfg: SimpleDogCurrentV3RoughEnvCfg

    def __init__(self, cfg, render_mode=None, **kwargs):
        super().__init__(cfg, render_mode, **kwargs)
        joint_shape = (self.num_envs, self.cfg.action_space)
        self._posture_commands = torch.zeros(self.num_envs, 3, device=self.device)
        self._posture_targets = torch.zeros_like(self._posture_commands)
        self._posture_steps_remaining = torch.zeros(
            self.num_envs, dtype=torch.long, device=self.device
        )

        maximum_action_delay = self.cfg.action_delay_steps[1]
        self._action_delay_buffer = torch.zeros(
            self.num_envs,
            maximum_action_delay + 1,
            self.cfg.action_space,
            device=self.device,
        )
        self._action_delay = torch.zeros(
            joint_shape, dtype=torch.long, device=self.device
        )
        self._position_error = torch.zeros(joint_shape, device=self.device)
        self._last_simulated_target = self._robot.data.default_joint_pos.torch[
            :, self._policy_joint_ids
        ].clone()

        maximum_current_delay = self.cfg.current_delay_steps[1]
        self._current_delay_buffer = torch.zeros(
            self.num_envs,
            maximum_current_delay + 1,
            self.cfg.action_space,
            device=self.device,
        )
        self._current_delay = torch.zeros(
            joint_shape, dtype=torch.long, device=self.device
        )
        self._current_dropout_probability = torch.zeros(joint_shape, device=self.device)
        self._current_effort_scale = torch.ones(joint_shape, device=self.device)
        self._last_finite_current = torch.zeros(joint_shape, device=self.device)
        self._current_history = torch.zeros(
            self.num_envs,
            self.cfg.observation_history_length,
            2 * self.cfg.action_space,
            device=self.device,
        )
        self._current_history_ready = torch.zeros(
            self.num_envs, dtype=torch.bool, device=self.device
        )
        self._latest_current_validity = torch.ones(joint_shape, device=self.device)
        self._latest_normalized_current = torch.zeros(joint_shape, device=self.device)
        self._reset_settle_steps_remaining = torch.zeros(
            self.num_envs, dtype=torch.long, device=self.device
        )
        self._reset_hold_active_mask = torch.zeros(
            self.num_envs, dtype=torch.bool, device=self.device
        )
        self._reset_hold_joint_position = self._robot.data.default_joint_pos.torch[
            :, self._policy_joint_ids
        ].clone()

        self._fit_current_bias = self._joint_tensor(self.cfg.current_bias_ma)
        self._fit_current_scale = self._joint_tensor(self.cfg.current_scale_ma)
        self._fit_current_clip = self._joint_tensor(self.cfg.current_clip_ma)
        self._fit_current_noise = self._joint_tensor(self.cfg.current_noise_mad_ma)
        self._fit_speed_limit = self._joint_tensor(self.cfg.actuator_speed_limit_rad_s)
        self._fit_residual_bias = self._joint_tensor(self.cfg.actuator_residual_bias_rad)
        self._fit_residual_mad = self._joint_tensor(self.cfg.actuator_residual_mad_rad)

        self._episode_sums["posture_tracking"] = torch.zeros(
            self.num_envs, device=self.device
        )
        self._episode_sums["posture_attitude_error"] = torch.zeros(
            self.num_envs, device=self.device
        )
        self._evaluation_posture_height_sum = 0.0
        self._evaluation_posture_roll_sum = 0.0
        self._evaluation_posture_pitch_sum = 0.0
        self._evaluation_posture_height_error_sum = 0.0
        self._evaluation_posture_roll_error_sum = 0.0
        self._evaluation_posture_pitch_error_sum = 0.0
        self._evaluation_current_valid_sum = 0.0

        all_envs = torch.arange(self.num_envs, device=self.device)
        self._sample_current_model(all_envs)
        self._sample_posture_targets(all_envs, immediate=True)
        self._start_reset_settle(all_envs)

    def _start_reset_settle(self, env_ids: torch.Tensor) -> None:
        """Drop in a randomized held pose before handing control to PPO."""

        settle_steps = max(
            0, round(self.cfg.reset_settle_time_s / self.step_dt)
        )
        default_position = self._robot.data.default_joint_pos.torch[
            env_ids[:, None], self._policy_joint_ids
        ].clone()
        randomization = self.cfg.reset_hold_randomization_rad
        if settle_steps and randomization > 0.0:
            default_position += torch.empty_like(default_position).uniform_(
                -randomization, randomization
            )
        joint_limits = self._robot.data.joint_pos_limits.torch[
            env_ids[:, None], self._policy_joint_ids
        ]
        hold_position = torch.maximum(
            torch.minimum(default_position, joint_limits[:, :, 1]),
            joint_limits[:, :, 0],
        )
        self._reset_hold_joint_position[env_ids] = hold_position
        self._reset_settle_steps_remaining[env_ids] = settle_steps
        self._reset_hold_active_mask[env_ids] = settle_steps > 0

        full_joint_position = self._robot.data.default_joint_pos.torch[
            env_ids
        ].clone()
        full_joint_position[:, self._policy_joint_ids] = hold_position
        full_joint_velocity = torch.zeros_like(
            self._robot.data.default_joint_vel.torch[env_ids]
        )
        self._robot.write_joint_position_to_sim_index(
            position=full_joint_position, env_ids=env_ids
        )
        self._robot.write_joint_velocity_to_sim_index(
            velocity=full_joint_velocity, env_ids=env_ids
        )
        self._actuator_target_state[env_ids] = hold_position
        self._last_simulated_target[env_ids] = hold_position
        if self.cfg.print_play_metrics and torch.any(env_ids == 0):
            print(
                "RESET_DROP "
                f"clearance_m={self.cfg.reset_spawn_clearance_m:.3f} "
                f"settle_steps={settle_steps} "
                f"settle_seconds={settle_steps * self.step_dt:.3f} "
                f"joint_randomization_deg="
                f"{math.degrees(randomization):.1f}",
                flush=True,
            )

    def _get_dones(self) -> tuple[torch.Tensor, torch.Tensor]:
        """Do not terminate an episode during the requested locked drop."""

        terminated, time_out = super()._get_dones()
        if hasattr(self, "_reset_hold_active_mask"):
            terminated = terminated & ~self._reset_hold_active_mask
        return terminated, time_out

    def _joint_tensor(self, values) -> torch.Tensor:
        if len(values) != self.cfg.action_space:
            raise ValueError("CurrentV3 fit arrays must match the action size")
        return torch.tensor(values, dtype=self._actions.dtype, device=self.device).unsqueeze(0)

    def _random_delays(self, shape, limits) -> torch.Tensor:
        minimum, maximum = limits
        return torch.randint(
            minimum,
            maximum + 1,
            shape,
            dtype=torch.long,
            device=self.device,
        )

    def _sample_current_model(self, env_ids: torch.Tensor) -> None:
        shape = (len(env_ids), self.cfg.action_space)
        self._action_delay[env_ids] = self._random_delays(shape, self.cfg.action_delay_steps)
        self._current_delay[env_ids] = self._random_delays(shape, self.cfg.current_delay_steps)
        self._current_dropout_probability[env_ids].uniform_(
            0.0, self.cfg.current_dropout_probability_max
        )
        self._current_effort_scale[env_ids].uniform_(
            *self.cfg.current_effort_scale_randomization
        )
        residual = torch.randn(shape, device=self.device) * (
            1.4826 * self._fit_residual_mad
        )
        self._position_error[env_ids] = self._fit_residual_bias + residual

    def _sample_posture_targets(self, env_ids: torch.Tensor, *, immediate: bool) -> None:
        count = len(env_ids)
        targets = torch.empty(count, 3, device=self.device)
        targets[:, 0].uniform_(*self.cfg.posture_height_offset)
        targets[:, 1].uniform_(*self.cfg.posture_roll)
        targets[:, 2].uniform_(*self.cfg.posture_pitch)
        neutral = torch.rand(count, device=self.device) < self.cfg.posture_neutral_fraction
        targets[neutral] = 0.0
        self._posture_targets[env_ids] = targets
        self._posture_steps_remaining[env_ids] = self._random_step_counts(
            count, self.cfg.posture_hold_s
        )
        if immediate:
            self._posture_commands[env_ids] = targets

    def _apply_posture_commands(self) -> None:
        if self._evaluation_segments:
            elapsed = self._play_step_count
            segment = self._evaluation_segments[-1]
            for candidate in self._evaluation_segments:
                if elapsed < int(candidate[1]):
                    segment = candidate
                    break
                elapsed -= int(candidate[1])
            self._posture_targets[:] = torch.tensor(
                segment[5:8], device=self.device, dtype=self._actions.dtype
            )
        else:
            self._posture_steps_remaining -= 1
            due = torch.nonzero(
                self._posture_steps_remaining <= 0, as_tuple=False
            ).squeeze(-1)
            if len(due):
                self._sample_posture_targets(due, immediate=False)
        alpha = min(
            1.0,
            self.step_dt / max(self.cfg.posture_smoothing_time_s, self.step_dt),
        )
        self._posture_commands += alpha * (
            self._posture_targets - self._posture_commands
        )

    def _pre_physics_step(self, actions: torch.Tensor):
        settling = self._reset_settle_steps_remaining > 0
        actions = torch.where(settling.unsqueeze(1), 0.0, actions)
        self._apply_posture_commands()
        self._action_delay_buffer = torch.roll(
            self._action_delay_buffer, shifts=-1, dims=1
        )
        self._action_delay_buffer[:, -1] = actions
        maximum_delay = self.cfg.action_delay_steps[1]
        gather_index = (maximum_delay - self._action_delay).unsqueeze(1)
        delayed_actions = torch.gather(
            self._action_delay_buffer, 1, gather_index
        ).squeeze(1)
        super()._pre_physics_step(delayed_actions)

        # V2 owns the filtered actuator state. Apply only the lag-aligned
        # residual and measured speed bound to the target written to PhysX.
        target = self._actuator_target_state - self._position_error
        maximum_delta = self._fit_speed_limit * self.step_dt
        target = torch.clamp(
            target,
            self._last_simulated_target - maximum_delta,
            self._last_simulated_target + maximum_delta,
        )
        target[settling] = self._reset_hold_joint_position[settling]
        self._last_simulated_target = target.clone()
        self._processed_actions = target
        self._reset_hold_active_mask = settling
        if torch.any(settling):
            self._actions[settling] = 0.0
            self._previous_actions[settling] = 0.0
            self._raw_actions[settling] = 0.0
            self._previous_raw_actions[settling] = 0.0
            self._filtered_actions[settling] = 0.0
            self._previous_filtered_actions[settling] = 0.0
            self._actuator_target_state[settling] = self._reset_hold_joint_position[
                settling
            ]
            releasing = settling & (self._reset_settle_steps_remaining == 1)
            self._reset_settle_steps_remaining[settling] -= 1
            if self.cfg.print_play_metrics and bool(releasing[0].item()):
                print("POLICY_CONTROL_ENABLED", flush=True)

    def _simulate_current(self) -> tuple[torch.Tensor, torch.Tensor]:
        effort = torch.abs(
            self._robot.data.applied_torque.torch[:, self._policy_joint_ids]
        )
        effort_limit = self._robot.data.joint_effort_limits.torch[
            :, self._policy_joint_ids
        ].clamp_min(1.0e-6)
        effort_fraction = torch.clamp(effort / effort_limit, 0.0, 1.5)
        current_ma = self._fit_current_bias + (
            effort_fraction * self._fit_current_scale * self._current_effort_scale
        )
        current_ma += torch.empty_like(current_ma).uniform_(-1.0, 1.0) * (
            self._fit_current_noise
        )
        current_ma = torch.minimum(torch.clamp_min(current_ma, 0.0), self._fit_current_clip)
        normalized = torch.minimum(
            torch.clamp_min(
                (current_ma - self._fit_current_bias) / self._fit_current_scale,
                0.0,
            ),
            self._fit_current_clip / self._fit_current_scale,
        )

        self._current_delay_buffer = torch.roll(
            self._current_delay_buffer, shifts=-1, dims=1
        )
        self._current_delay_buffer[:, -1] = normalized
        maximum_delay = self.cfg.current_delay_steps[1]
        gather_index = (maximum_delay - self._current_delay).unsqueeze(1)
        delayed = torch.gather(
            self._current_delay_buffer, 1, gather_index
        ).squeeze(1)
        validity = (
            torch.rand_like(delayed) >= self._current_dropout_probability
        )
        if self.cfg.force_current_dropout_pattern and self._evaluation_segments:
            segment = self._evaluation_segments[
                max(0, self._evaluation_segment_index)
            ]
            if segment[0] == "current_dropout_walk":
                validity &= (self._play_step_count % 5) != 0
        held = torch.where(validity, delayed, self._last_finite_current)
        self._last_finite_current = held.clone()
        self._latest_current_validity = validity.float()
        self._latest_normalized_current = held
        return held, validity.float()

    def _get_observations(self) -> dict:
        super()._get_observations()
        current, validity = self._simulate_current()
        current_frame = torch.cat((current, validity), dim=1)
        self._current_history = torch.roll(
            self._current_history, shifts=-1, dims=1
        )
        self._current_history[:, -1] = current_frame
        fresh = ~self._current_history_ready
        if torch.any(fresh):
            self._current_history[fresh] = current_frame[fresh].unsqueeze(1).expand(
                -1, self.cfg.observation_history_length, -1
            )
            self._current_history_ready[fresh] = True
        combined_history = torch.cat(
            (self._observation_history, self._current_history), dim=2
        ).flatten(start_dim=1)
        observation = torch.cat((combined_history, self._posture_commands), dim=1)
        if observation.shape[1] != self.cfg.observation_space:
            raise RuntimeError(
                f"CurrentV3 observation mismatch: expected {self.cfg.observation_space}, "
                f"received {observation.shape[1]}"
            )
        return {"policy": observation}

    def _body_posture(self) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        projected = self._semantic_vector_b(
            self._robot.data.projected_gravity_b.torch
        )
        roll = torch.atan2(projected[:, 1], -projected[:, 2])
        pitch = torch.atan2(
            -projected[:, 0],
            torch.sqrt(torch.square(projected[:, 1]) + torch.square(projected[:, 2])),
        )
        foot_z = self._robot.data.body_com_pos_w.torch[:, self._feet_body_ids, 2]
        support_z = torch.topk(foot_z, k=2, dim=1, largest=False).values.mean(dim=1)
        height = self._robot.data.root_pos_w.torch[:, 2] - support_z
        return height, roll, pitch

    def _get_rewards(self) -> torch.Tensor:
        reward = super()._get_rewards()
        height, roll, pitch = self._body_posture()
        desired_height = self.cfg.nominal_support_height_m + self._posture_commands[:, 0]
        height_score = torch.exp(
            -torch.abs(height - desired_height) / self.cfg.posture_height_tracking_std
        )
        roll_score = torch.exp(
            -torch.abs(roll - self._posture_commands[:, 1])
            / self.cfg.posture_angle_tracking_std
        )
        pitch_score = torch.exp(
            -torch.abs(pitch - self._posture_commands[:, 2])
            / self.cfg.posture_angle_tracking_std
        )
        posture = (height_score + roll_score + pitch_score) / 3.0
        term = posture * self.cfg.posture_tracking_reward_scale * self.step_dt
        attitude_error = (
            torch.abs(roll - self._posture_commands[:, 1])
            + torch.abs(pitch - self._posture_commands[:, 2])
        )
        attitude_term = (
            attitude_error
            * self.cfg.posture_attitude_error_penalty_scale
            * self.step_dt
        )
        self._episode_sums["posture_tracking"] += term
        self._episode_sums["posture_attitude_error"] += attitude_term
        return reward + term + attitude_term

    def _begin_evaluation_segment(self, segment_index: int) -> None:
        self._evaluation_posture_height_sum = 0.0
        self._evaluation_posture_roll_sum = 0.0
        self._evaluation_posture_pitch_sum = 0.0
        self._evaluation_posture_height_error_sum = 0.0
        self._evaluation_posture_roll_error_sum = 0.0
        self._evaluation_posture_pitch_error_sum = 0.0
        self._evaluation_current_valid_sum = 0.0
        super()._begin_evaluation_segment(segment_index)

    def _record_evaluation_step(self, **kwargs) -> None:
        height, roll, pitch = self._body_posture()
        desired_height = self.cfg.nominal_support_height_m + self._posture_commands[0, 0]
        self._evaluation_posture_height_sum += height[0].item()
        self._evaluation_posture_roll_sum += roll[0].item()
        self._evaluation_posture_pitch_sum += pitch[0].item()
        self._evaluation_posture_height_error_sum += abs(
            height[0].item() - desired_height.item()
        )
        self._evaluation_posture_roll_error_sum += abs(
            roll[0].item() - self._posture_commands[0, 1].item()
        )
        self._evaluation_posture_pitch_error_sum += abs(
            pitch[0].item() - self._posture_commands[0, 2].item()
        )
        self._evaluation_current_valid_sum += self._latest_current_validity[0].mean().item()
        super()._record_evaluation_step(**kwargs)

    def _evaluation_extra_metrics(self, steps: int, segment) -> str:
        return (
            f"command_height_offset={float(segment[5]):.4f} "
            f"command_roll={float(segment[6]):.4f} "
            f"command_pitch={float(segment[7]):.4f} "
            f"mean_support_height={self._evaluation_posture_height_sum / steps:.4f} "
            f"mean_roll={self._evaluation_posture_roll_sum / steps:.4f} "
            f"mean_pitch={self._evaluation_posture_pitch_sum / steps:.4f} "
            f"mean_abs_height_error={self._evaluation_posture_height_error_sum / steps:.4f} "
            f"mean_abs_roll_error={self._evaluation_posture_roll_error_sum / steps:.4f} "
            f"mean_abs_pitch_error={self._evaluation_posture_pitch_error_sum / steps:.4f} "
            f"mean_current_valid_fraction={self._evaluation_current_valid_sum / steps:.4f} "
        )

    def _reset_idx(self, env_ids: torch.Tensor | None):
        if env_ids is None:
            env_ids = torch.arange(self.num_envs, dtype=torch.long, device=self.device)
        super()._reset_idx(env_ids)
        if not hasattr(self, "_posture_commands"):
            return
        self._sample_current_model(env_ids)
        self._sample_posture_targets(env_ids, immediate=True)
        self._action_delay_buffer[env_ids] = 0.0
        self._current_delay_buffer[env_ids] = 0.0
        self._current_history[env_ids] = 0.0
        self._current_history_ready[env_ids] = False
        self._last_finite_current[env_ids] = 0.0
        default_policy_position = self._robot.data.default_joint_pos.torch[
            :, self._policy_joint_ids
        ]
        self._last_simulated_target[env_ids] = default_policy_position[env_ids]
        self._start_reset_settle(env_ids)

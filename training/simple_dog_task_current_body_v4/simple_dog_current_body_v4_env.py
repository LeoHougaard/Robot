"""CurrentBodyV4 mixed-command body control with deployable dual history."""

from __future__ import annotations

import math

import torch

from isaaclab.utils.math import quat_apply

from simple_dog_task_current.simple_dog_current_env import SimpleDogCurrentV3Env
from v4_difficulty_ramp import difficulty_fraction, scheduled_terrain_level
from .simple_dog_current_body_v4_env_cfg import (
    SimpleDogCurrentBodyV4HardEnvCfg,
)


class SimpleDogCurrentBodyV4Env(SimpleDogCurrentV3Env):
    cfg: SimpleDogCurrentBodyV4HardEnvCfg

    def __init__(self, cfg, render_mode=None, **kwargs):
        super().__init__(cfg, render_mode, **kwargs)
        self._v4_full_actuator_response_alpha = (
            self._actuator_response_alpha.clone()
        )
        self._v4_nominal_actuator_response_alpha = torch.tensor(
            self.cfg.actuator_response_alpha_by_joint,
            dtype=self._actuator_response_alpha.dtype,
            device=self.device,
        ).unsqueeze(0).expand_as(self._actuator_response_alpha)
        self._v4_last_response_difficulty = -1.0
        self._update_ramped_actuator_response()
        self._timing_history = torch.ones(
            self.num_envs,
            self.cfg.observation_history_length,
            1,
            device=self.device,
        )
        self._v4_command_modes = torch.zeros(
            self.num_envs, dtype=torch.long, device=self.device
        )
        self._current_model_weights = torch.zeros(
            self.num_envs, 4, device=self.device
        )
        self._sample_current_model(
            torch.arange(self.num_envs, dtype=torch.long, device=self.device)
        )
        self._episode_sums["body_tracking"] = torch.zeros(
            self.num_envs, device=self.device
        )
        self._episode_sums["body_motion_shortfall"] = torch.zeros(
            self.num_envs, device=self.device
        )
        all_envs = torch.arange(
            self.num_envs, dtype=torch.long, device=self.device
        )
        self._sample_command_targets(all_envs, immediate=True)
        self._sample_posture_targets(all_envs, immediate=True)

    def _difficulty_fraction(self) -> float:
        return difficulty_fraction(
            int(getattr(self, "common_step_counter", 0)),
            self.cfg.difficulty_ramp_full_step,
            self.cfg.difficulty_ramp_floor,
        )

    def _update_ramped_actuator_response(self) -> None:
        if not hasattr(self, "_v4_full_actuator_response_alpha"):
            return
        difficulty = self._difficulty_fraction()
        if abs(difficulty - self._v4_last_response_difficulty) < 0.002:
            return
        self._actuator_response_alpha = (
            self._v4_nominal_actuator_response_alpha
            + difficulty
            * (
                self._v4_full_actuator_response_alpha
                - self._v4_nominal_actuator_response_alpha
            )
        )
        self._v4_last_response_difficulty = difficulty

    def _place_resets_on_scheduled_terrain(self, env_ids: torch.Tensor) -> None:
        if (
            not hasattr(self, "_terrain")
            or self._terrain.terrain_origins is None
            or not hasattr(self._terrain, "terrain_levels")
        ):
            return
        terrain_rows = int(self._terrain.terrain_origins.shape[0])
        level = scheduled_terrain_level(
            int(getattr(self, "common_step_counter", 0)),
            self.cfg.difficulty_ramp_full_step,
            terrain_rows,
            self.cfg.difficulty_ramp_floor,
        )
        band_rows = min(
            self.cfg.difficulty_ramp_terrain_band_rows, terrain_rows
        )
        upper_level = max(level, band_rows - 1)
        lower_level = max(0, upper_level - band_rows + 1)
        self._terrain.terrain_levels[env_ids] = torch.randint(
            lower_level,
            upper_level + 1,
            (len(env_ids),),
            device=self.device,
        )
        self._terrain.env_origins[env_ids] = self._terrain.terrain_origins[
            self._terrain.terrain_levels[env_ids],
            self._terrain.terrain_types[env_ids],
        ]

    def _pre_physics_step(self, actions: torch.Tensor):
        self._update_ramped_actuator_response()
        super()._pre_physics_step(actions)

    def _apply_random_pushes(self) -> None:
        difficulty = self._difficulty_fraction()
        probability = self.cfg.push_probability * difficulty
        if probability <= 0.0:
            return

        self._push_steps_remaining -= 1
        due = torch.nonzero(
            self._push_steps_remaining <= 0, as_tuple=False
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

        root_velocity = torch.cat(
            (
                self._robot.data.root_lin_vel_w.torch[selected],
                self._robot.data.root_ang_vel_w.torch[selected],
            ),
            dim=1,
        ).clone()
        root_velocity[:, :2] += torch.empty(
            len(selected), 2, device=self.device
        ).uniform_(
            -self.cfg.push_linear_velocity * difficulty,
            self.cfg.push_linear_velocity * difficulty,
        )
        root_velocity[:, 5] += torch.empty(
            len(selected), device=self.device
        ).uniform_(
            -self.cfg.push_yaw_velocity * difficulty,
            self.cfg.push_yaw_velocity * difficulty,
        )
        self._robot.write_root_velocity_to_sim_index(
            root_velocity=root_velocity, env_ids=selected
        )

    def _apply_startup_domain_randomization(self) -> None:
        super()._apply_startup_domain_randomization()
        if not self.cfg.domain_randomization_enabled:
            return
        low, high = self.cfg.independent_inertia_scale
        env_ids = torch.arange(
            self.num_envs, dtype=torch.int32, device=self.device
        )
        body_ids = torch.arange(
            len(self._robot.body_names), dtype=torch.int32, device=self.device
        )
        inertia = self._robot.data.body_inertia.torch[
            env_ids[:, None], body_ids
        ].clone()
        scale = torch.exp(
            torch.empty(
                (self.num_envs, len(self._robot.body_names), 1),
                device=self.device,
            ).uniform_(math.log(low), math.log(high))
        )
        # Scale each complete body tensor uniformly. Independent axis scaling
        # can violate the rigid-body inertia triangle inequalities even when
        # the resulting matrix remains positive definite.
        inertia *= scale
        self._robot.set_inertias_index(
            inertias=inertia, body_ids=body_ids, env_ids=env_ids
        )

    def _sample_current_model(self, env_ids: torch.Tensor) -> None:
        difficulty = self._difficulty_fraction()
        original_dropout = self.cfg.current_dropout_probability_max
        original_effort_scale = self.cfg.current_effort_scale_randomization
        self.cfg.current_dropout_probability_max = original_dropout * difficulty
        self.cfg.current_effort_scale_randomization = (
            1.0 + difficulty * (original_effort_scale[0] - 1.0),
            1.0 + difficulty * (original_effort_scale[1] - 1.0),
        )
        try:
            super()._sample_current_model(env_ids)
        finally:
            self.cfg.current_dropout_probability_max = original_dropout
            self.cfg.current_effort_scale_randomization = original_effort_scale
        if not hasattr(self, "_current_model_weights"):
            return
        ranges = (
            self.cfg.current_model_effort_weight,
            self.cfg.current_model_tracking_weight,
            self.cfg.current_model_velocity_weight,
            self.cfg.current_model_memory_weight,
        )
        sampled = torch.empty(
            len(env_ids), 4, device=self.device
        )
        for index, bounds in enumerate(ranges):
            sampled[:, index].uniform_(*bounds)
        self._current_model_weights[env_ids] = sampled / sampled.sum(
            dim=1, keepdim=True
        )

    @staticmethod
    def _signed_magnitude(
        count: int, minimum: float, maximum: float, device
    ) -> torch.Tensor:
        magnitude = torch.empty(count, device=device).uniform_(minimum, maximum)
        sign = torch.where(
            torch.rand(count, device=device) < 0.5,
            -torch.ones(count, device=device),
            torch.ones(count, device=device),
        )
        return magnitude * sign

    def _sample_command_targets(
        self, env_ids: torch.Tensor, *, immediate: bool
    ) -> None:
        count = len(env_ids)
        targets = torch.zeros(count, 3, device=self.device)
        sample = torch.rand(count, device=self.device)
        mixed_end = self.cfg.mixed_command_fraction
        posture_end = mixed_end + self.cfg.posture_only_fraction
        isolated_end = posture_end + self.cfg.isolated_motion_fraction
        modes = torch.full(
            (count,), 3, dtype=torch.long, device=self.device
        )
        modes[sample < mixed_end] = 0
        modes[(sample >= mixed_end) & (sample < posture_end)] = 1
        modes[(sample >= posture_end) & (sample < isolated_end)] = 2
        if hasattr(self, "_v4_command_modes"):
            self._v4_command_modes[env_ids] = modes

        mixed = modes == 0
        mixed_count = int(mixed.sum().item())
        if mixed_count:
            targets[mixed, 0] = self._signed_magnitude(
                mixed_count, 0.05, max(abs(v) for v in self.cfg.command_forward_v4), self.device
            )
            targets[mixed, 1] = self._signed_magnitude(
                mixed_count, 0.04, max(abs(v) for v in self.cfg.command_lateral_v4), self.device
            )
            targets[mixed, 2] = self._signed_magnitude(
                mixed_count, 0.08, max(abs(v) for v in self.cfg.command_yaw_v4), self.device
            )

        isolated = modes == 2
        isolated_ids = torch.nonzero(isolated, as_tuple=False).squeeze(-1)
        if len(isolated_ids):
            axis_sample = torch.rand(len(isolated_ids), device=self.device)
            forward_end = (
                self.cfg.isolated_linear_axis_fraction
                * self.cfg.isolated_forward_share
            )
            lateral_end = self.cfg.isolated_linear_axis_fraction
            axes = torch.where(
                axis_sample < forward_end,
                torch.zeros_like(axis_sample, dtype=torch.long),
                torch.where(
                    axis_sample < lateral_end,
                    torch.ones_like(axis_sample, dtype=torch.long),
                    torch.full_like(axis_sample, 2, dtype=torch.long),
                ),
            )
            for axis, minimum, maximum in (
                (0, 0.05, max(abs(v) for v in self.cfg.command_forward_v4)),
                (1, 0.04, max(abs(v) for v in self.cfg.command_lateral_v4)),
                (2, 0.08, max(abs(v) for v in self.cfg.command_yaw_v4)),
            ):
                selected = isolated_ids[axes == axis]
                if len(selected):
                    targets[selected, axis] = self._signed_magnitude(
                        len(selected), minimum, maximum, self.device
                    )

        tail = (
            torch.rand(count, device=self.device)
            < self.cfg.capability_tail_fraction
        ) & (modes != 3)
        targets[tail] *= self.cfg.capability_tail_scale
        self._command_targets[env_ids] = targets
        hold_steps = self._random_step_counts(
            count, self.cfg.command_hold_s
        )
        linear = torch.linalg.vector_norm(targets[:, :2], dim=1) > 0.0
        linear_count = int(linear.sum().item())
        if linear_count:
            hold_steps[linear] = self._random_step_counts(
                linear_count, self.cfg.linear_command_hold_s
            )
        self._command_steps_remaining[env_ids] = hold_steps
        if immediate:
            self._commands[env_ids] = targets
        if hasattr(self, "_posture_targets"):
            self._sample_posture_targets(env_ids, immediate=immediate)

    def _sample_posture_targets(
        self, env_ids: torch.Tensor, *, immediate: bool
    ) -> None:
        count = len(env_ids)
        targets = torch.zeros(count, 3, device=self.device)
        if hasattr(self, "_v4_command_modes"):
            modes = self._v4_command_modes[env_ids]
        else:
            modes = torch.zeros(count, dtype=torch.long, device=self.device)
        active = (modes == 0) | (modes == 1)
        active_count = int(active.sum().item())
        if active_count:
            targets[active, 0] = self._signed_magnitude(
                active_count, 0.006, max(abs(v) for v in self.cfg.posture_height_offset), self.device
            )
            targets[active, 1] = self._signed_magnitude(
                active_count, 0.025, max(abs(v) for v in self.cfg.posture_roll), self.device
            )
            targets[active, 2] = self._signed_magnitude(
                active_count, 0.025, max(abs(v) for v in self.cfg.posture_pitch), self.device
            )
        tail = (
            torch.rand(count, device=self.device)
            < self.cfg.capability_tail_fraction
        ) & active
        targets[tail] *= self.cfg.capability_tail_scale
        self._posture_targets[env_ids] = targets
        self._posture_steps_remaining[env_ids] = self._command_steps_remaining[
            env_ids
        ]
        if immediate:
            self._posture_commands[env_ids] = targets

    def _apply_posture_commands(self) -> None:
        if self._evaluation_segments:
            return super()._apply_posture_commands()
        alpha = min(
            1.0,
            self.step_dt / max(self.cfg.posture_smoothing_time_s, self.step_dt),
        )
        self._posture_commands += alpha * (
            self._posture_targets - self._posture_commands
        )

    def _simulate_current(self) -> tuple[torch.Tensor, torch.Tensor]:
        effort = torch.abs(
            self._robot.data.applied_torque.torch[:, self._policy_joint_ids]
        )
        effort_limit = self._robot.data.joint_effort_limits.torch[
            :, self._policy_joint_ids
        ].clamp_min(1.0e-6)
        effort_fraction = torch.clamp(effort / effort_limit, 0.0, 1.5)
        joint_position = self._robot.data.joint_pos.torch[
            :, self._policy_joint_ids
        ]
        joint_velocity = self._robot.data.joint_vel.torch[
            :, self._policy_joint_ids
        ]
        tracking = torch.clamp(
            torch.abs(self._last_simulated_target - joint_position)
            / (3.0 * self._fit_residual_mad).clamp_min(0.03),
            0.0,
            2.0,
        )
        velocity = torch.clamp(
            torch.abs(joint_velocity) / self._fit_speed_limit.clamp_min(0.1),
            0.0,
            2.0,
        )
        weights = self._current_model_weights
        normalized_source = (
            weights[:, 0:1] * effort_fraction
            + weights[:, 1:2] * tracking
            + weights[:, 2:3] * velocity
            + weights[:, 3:4] * self._last_finite_current
        )
        current_ma = self._fit_current_bias + (
            normalized_source
            * self._fit_current_scale
            * self._current_effort_scale
        )
        current_ma += torch.empty_like(current_ma).uniform_(-1.0, 1.0) * (
            self._fit_current_noise
        )
        current_ma = torch.minimum(
            torch.clamp_min(current_ma, 0.0), self._fit_current_clip
        )
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
        validity = torch.rand_like(delayed) >= self._current_dropout_probability
        held = torch.where(validity, delayed, self._last_finite_current)
        self._last_finite_current = held.clone()
        self._latest_current_validity = validity.float()
        self._latest_normalized_current = held
        return held, validity.float()

    def _get_observations(self) -> dict:
        difficulty = self._difficulty_fraction()
        angular_velocity = self._semantic_vector_b(
            self._robot.data.root_ang_vel_b.torch
        ).clone()
        projected_gravity = self._semantic_vector_b(
            self._robot.data.projected_gravity_b.torch
        ).clone()
        joint_position, joint_velocity = self._get_policy_joint_state()
        joint_position = joint_position.clone()
        joint_velocity = joint_velocity.clone()

        if self.cfg.observation_noise_enabled:
            angular_velocity += torch.empty_like(angular_velocity).uniform_(
                -self.cfg.gyro_noise * difficulty,
                self.cfg.gyro_noise * difficulty,
            )
            projected_gravity += torch.empty_like(projected_gravity).uniform_(
                -self.cfg.gravity_noise * difficulty,
                self.cfg.gravity_noise * difficulty,
            )
            joint_position += torch.empty_like(joint_position).uniform_(
                -self.cfg.joint_position_noise * difficulty,
                self.cfg.joint_position_noise * difficulty,
            )
            joint_velocity += torch.empty_like(joint_velocity).uniform_(
                -self.cfg.joint_velocity_noise * difficulty,
                self.cfg.joint_velocity_noise * difficulty,
            )

        frame = torch.cat(
            (
                angular_velocity,
                projected_gravity,
                self._commands,
                joint_position,
                0.05 * joint_velocity,
                self._actions,
            ),
            dim=1,
        )
        if frame.shape[1] != self.cfg.observation_frame_size:
            raise RuntimeError(
                "V4 observation frame mismatch: "
                f"expected {self.cfg.observation_frame_size}, "
                f"received {frame.shape[1]}"
            )
        self._observation_history = torch.roll(
            self._observation_history, shifts=-1, dims=1
        )
        self._observation_history[:, -1] = frame
        fresh_policy = ~self._history_ready
        if torch.any(fresh_policy):
            self._observation_history[fresh_policy] = frame[
                fresh_policy
            ].unsqueeze(1).expand(
                -1, self.cfg.observation_history_length, -1
            )
            self._history_ready[fresh_policy] = True
        self._update_video_camera()
        current, validity = self._simulate_current()
        current_frame = torch.cat((current, validity), dim=1)
        self._current_history = torch.roll(
            self._current_history, shifts=-1, dims=1
        )
        self._current_history[:, -1] = current_frame
        interval = torch.empty(
            self.num_envs, 1, device=self.device
        ).uniform_(*self.cfg.timing_interval_ms)
        if not self.cfg.domain_randomization_enabled:
            interval.fill_(self.cfg.timing_reference_ms)
        timing = interval / self.cfg.timing_reference_ms
        self._timing_history = torch.roll(
            self._timing_history, shifts=-1, dims=1
        )
        self._timing_history[:, -1] = timing
        fresh = ~self._current_history_ready
        if torch.any(fresh):
            self._current_history[fresh] = current_frame[fresh].unsqueeze(1).expand(
                -1, self.cfg.observation_history_length, -1
            )
            self._timing_history[fresh] = timing[fresh].unsqueeze(1).expand(
                -1, self.cfg.observation_history_length, -1
            )
            self._current_history_ready[fresh] = True
        full_history = torch.cat(
            (self._observation_history, self._current_history, self._timing_history),
            dim=2,
        )
        selected = full_history[:, self.cfg.selected_history_indices].flatten(
            start_dim=1
        )
        body_command = torch.cat(
            (self._commands, self._posture_commands), dim=1
        )
        observation = torch.cat((selected, body_command), dim=1)
        if observation.shape[1] != self.cfg.observation_space:
            raise RuntimeError(
                "CurrentBodyV4 observation mismatch: "
                f"expected {self.cfg.observation_space}, received {observation.shape[1]}"
            )
        return {"policy": observation}

    def _get_rewards(self) -> torch.Tensor:
        body_forward, body_lateral, _, _, _, _ = self._get_physical_motion()
        angular_velocity = self._semantic_vector_b(
            self._robot.data.root_ang_vel_b.torch
        )[:, 2]
        height, roll, pitch = self._body_posture()
        desired_height = (
            self.cfg.nominal_support_height_m + self._posture_commands[:, 0]
        )
        actual = torch.stack(
            (body_forward, body_lateral, angular_velocity, height, roll, pitch),
            dim=1,
        )
        requested = torch.cat(
            (
                self._commands,
                desired_height.unsqueeze(1),
                self._posture_commands[:, 1:3],
            ),
            dim=1,
        )
        scales = torch.tensor(
            self.cfg.body_tracking_error_scales,
            dtype=actual.dtype,
            device=self.device,
        ).unsqueeze(0)
        normalized_error = (actual - requested) / scales
        smooth_absolute_error = torch.sqrt(
            torch.square(normalized_error) + 1.0e-4
        ) - 0.01
        tracking = torch.exp(
            -self.cfg.body_tracking_kernel_scale
            * smooth_absolute_error.mean(dim=1)
        )

        requested_motion = requested[:, :3] / scales[:, :3]
        actual_motion = actual[:, :3] / scales[:, :3]
        zero_error = torch.linalg.vector_norm(requested_motion, dim=1)
        motion_error = torch.linalg.vector_norm(
            requested_motion - actual_motion, dim=1
        )
        moving_gate = zero_error > self.cfg.body_motion_command_threshold
        progress_gate = torch.clamp(
            (zero_error - motion_error) / zero_error.clamp_min(1.0e-6),
            0.0,
            1.0,
        )
        motion_shortfall = (
            moving_gate.float()
            * (1.0 - progress_gate)
            * self.cfg.body_motion_shortfall_penalty_scale
        )
        settling = getattr(
            self,
            "_reset_hold_active_mask",
            torch.zeros(self.num_envs, dtype=torch.bool, device=self.device),
        )
        # V4 replaces the inherited locomotion reward, so it must also retain
        # the reward-independent episode diagnostics that the inherited
        # reset/logger consumes.  Without this, healthy multi-second episodes
        # were reported as zero-speed one-step failures even though RL-Games'
        # own episode-length series showed hundreds of steps.  Keep the
        # two-second locked drop in the survival duration, but exclude its
        # intentionally stationary motion from the tracking sums.
        active = (~settling).to(body_forward.dtype)
        self._survival_steps += 1.0
        self._velocity_error_sum += active * torch.linalg.vector_norm(
            requested[:, :2] - actual[:, :2], dim=1
        )
        self._world_forward_speed_sum += active * body_forward
        self._body_lateral_speed_sum += active * torch.abs(body_lateral)
        self._heading_error_sum += active * torch.abs(
            requested[:, 2] - angular_velocity
        )
        # Signed world/body speed can average to zero when a capable policy is
        # asked to move forward, reverse, and laterally in equal proportions.
        # Accumulate command-aligned distance as reward-independent evidence
        # of useful translation.  Exclude the intentionally stationary locked
        # drop just as the velocity diagnostics above do.
        requested_planar = requested[:, :2]
        actual_planar = actual[:, :2]
        commanded_speed = torch.linalg.vector_norm(requested_planar, dim=1)
        command_direction = requested_planar / commanded_speed.clamp_min(
            1.0e-6
        ).unsqueeze(1)
        aligned_speed = torch.sum(actual_planar * command_direction, dim=1)
        tracked_speed = torch.maximum(
            torch.minimum(aligned_speed, commanded_speed), -commanded_speed
        )
        self._terrain_commanded_distance += (
            active * commanded_speed * self.step_dt
        )
        self._terrain_tracked_distance += active * tracked_speed * self.step_dt
        reward = (tracking + motion_shortfall) * self.step_dt
        reward = torch.where(settling, 0.0, reward)
        self._episode_sums["body_tracking"] += torch.where(
            settling, 0.0, tracking * self.step_dt
        )
        self._episode_sums["body_motion_shortfall"] += torch.where(
            settling, 0.0, motion_shortfall * self.step_dt
        )
        if self.cfg.print_play_metrics and not bool(settling[0].item()):
            self._play_step_count += 1
        return reward

    def _get_dones(self) -> tuple[torch.Tensor, torch.Tensor]:
        terminated, time_out = super()._get_dones()
        # DirectRLEnv calls this after physics and before rewards/resets. V4+
        # replace the V2 reward, so evaluation must not depend on that reward.
        # The play counter still refers to the command that produced this step.
        if self._evaluation_segments and not bool(self._reset_hold_active_mask[0].item()):
            self._record_body_evaluation_step()
        return terminated, time_out

    def _record_body_evaluation_step(self) -> None:
        """Measure the same physical quantities as V2, without its reward."""
        data = self._robot.data
        linear = self._semantic_vector_b(data.root_lin_vel_b.torch)
        angular = self._semantic_vector_b(data.root_ang_vel_b.torch)
        gravity = self._semantic_vector_b(data.projected_gravity_b.torch)
        forces = self._contact_sensor.data.net_forces_w_history.torch
        contact = torch.linalg.vector_norm(
            forces[:, :, self._feet_sensor_ids], dim=-1
        ).amax(dim=1) > 1.0
        airborne = ~contact
        feet_velocity = data.body_lin_vel_w.torch[:, self._feet_body_ids, :2]
        slip = (torch.linalg.vector_norm(feet_velocity, dim=-1) * contact).sum(dim=1)
        slip /= contact.float().sum(dim=1).clamp_min(1.0)
        relative = data.body_com_pos_w.torch[:, self._feet_body_ids] - data.root_pos_w.torch.unsqueeze(1)
        # This is the existing body-relative clearance proxy, not a terrain raycast.
        clearance = ((relative[:, :, 2] - self.cfg.nominal_foot_com_z_from_base_m).clamp_min(0.0) * airborne).sum(dim=1)
        clearance /= airborne.float().sum(dim=1).clamp_min(1.0)
        positions, _ = self._get_policy_joint_state()
        abduction = positions[:, self._leg_policy_indices][:, :, 0].abs()
        lateral_axis = quat_apply(data.root_quat_w.torch, self._physical_lateral_axis_b)
        foot_lateral = (relative * lateral_axis.unsqueeze(1)).sum(dim=2)
        spread = ((foot_lateral - self._nominal_foot_lateral_m) * self._foot_outward_sign).clamp_min(0.0)
        delta = self._filtered_actions - self._previous_filtered_actions
        self._record_evaluation_step(
            body_forward=linear[:, 0], body_lateral=linear[:, 1],
            yaw_rate=angular[:, 2], foot_slip=slip,
            swing_foot_clearance=clearance,
            action_rate=delta.square().sum(dim=1),
            max_action_step=delta.abs().amax(dim=1),
            slew_clamp_fraction=self._action_slew_clamped.float().mean(dim=1),
            mean_hip_abduction=abduction.mean(dim=1),
            max_hip_abduction=abduction.amax(dim=1),
            mean_outward_foot_spread=spread.mean(dim=1),
            max_outward_foot_spread=spread.amax(dim=1),
            vertical_speed=linear[:, 2],
            tilt=torch.linalg.vector_norm(gravity[:, :2], dim=1),
            feet_contact=contact, root_height=data.root_pos_w.torch[:, 2],
        )

    def _reset_idx(self, env_ids: torch.Tensor | None):
        if env_ids is None:
            env_ids = torch.arange(
                self.num_envs, dtype=torch.long, device=self.device
            )
        self._place_resets_on_scheduled_terrain(env_ids)
        difficulty = self._difficulty_fraction()
        original_tilt = (
            self.cfg.reset_small_tilt_deg,
            self.cfg.reset_large_tilt_deg,
        )
        self.cfg.reset_small_tilt_deg = original_tilt[0] * difficulty
        self.cfg.reset_large_tilt_deg = original_tilt[1] * difficulty
        try:
            super()._reset_idx(env_ids)
        finally:
            (
                self.cfg.reset_small_tilt_deg,
                self.cfg.reset_large_tilt_deg,
            ) = original_tilt
        if hasattr(self, "_timing_history"):
            self._timing_history[env_ids] = 1.0

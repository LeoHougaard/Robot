"""Simple, deployable locomotion V2 for the eight-joint Onshape dog."""

from __future__ import annotations

import math

import torch

from isaaclab.utils.math import quat_from_euler_xyz

from simple_dog_task.simple_dog_env import SimpleDogEnv
from .simple_dog_v2_env_cfg import SimpleDogV2CoreEnvCfg


class SimpleDogV2Env(SimpleDogEnv):
    """Track smooth curved-path commands and recover from mild disturbances."""

    cfg: SimpleDogV2CoreEnvCfg

    def __init__(
        self,
        cfg: SimpleDogV2CoreEnvCfg,
        render_mode: str | None = None,
        **kwargs,
    ):
        super().__init__(cfg, render_mode, **kwargs)

        # A base strike already triggers the terminal fall penalty. Do not
        # count that same contact again as an undesired-link penalty.
        base_sensor_ids = set(self._base_sensor_ids)
        self._undesired_contact_sensor_ids = [
            sensor_id
            for sensor_id in self._undesired_contact_sensor_ids
            if sensor_id not in base_sensor_ids
        ]

        self._command_targets = self._commands.clone()
        self._command_steps_remaining = torch.zeros(
            self.num_envs, dtype=torch.long, device=self.device
        )
        self._push_steps_remaining = torch.zeros_like(
            self._command_steps_remaining
        )
        self._observation_history = torch.zeros(
            self.num_envs,
            self.cfg.observation_history_length,
            self.cfg.observation_frame_size,
            device=self.device,
        )
        self._history_ready = torch.zeros(
            self.num_envs, dtype=torch.bool, device=self.device
        )

        self._episode_sums = {
            key: torch.zeros(self.num_envs, device=self.device)
            for key in (
                "locomotion",
                "track_yaw_rate",
                "diagonal_gait",
                "stability",
                "action_rate",
                "foot_slip",
                "undesired_contact",
                "fall",
            )
        }

    def _random_step_counts(
        self, count: int, seconds: tuple[float, float]
    ) -> torch.Tensor:
        low = max(1, int(round(seconds[0] / self.step_dt)))
        high = max(low, int(round(seconds[1] / self.step_dt)))
        return torch.randint(
            low, high + 1, (count,), device=self.device, dtype=torch.long
        )

    def _sample_command_targets(
        self, env_ids: torch.Tensor, *, immediate: bool
    ) -> None:
        count = len(env_ids)
        targets = torch.zeros(count, 3, device=self.device)
        targets[:, 0].uniform_(*self.cfg.command_forward)
        targets[:, 2].uniform_(*self.cfg.command_yaw)
        mode_sample = torch.rand(count, device=self.device)
        turn_mask = mode_sample < self.cfg.turn_command_fraction
        stand_mask = (
            mode_sample
            >= self.cfg.turn_command_fraction
        ) & (
            mode_sample
            < self.cfg.turn_command_fraction
            + self.cfg.standing_command_fraction
        )
        turn_count = int(torch.sum(turn_mask).item())
        if turn_count:
            turn_magnitude = torch.empty(
                turn_count, device=self.device
            ).uniform_(*self.cfg.turn_yaw_rate)
            turn_sign = torch.where(
                torch.rand(turn_count, device=self.device) < 0.5,
                -torch.ones(turn_count, device=self.device),
                torch.ones(turn_count, device=self.device),
            )
            targets[turn_mask, 0] = 0.0
            targets[turn_mask, 2] = turn_magnitude * turn_sign
        targets[stand_mask] = 0.0
        self._command_targets[env_ids] = targets
        self._command_steps_remaining[env_ids] = self._random_step_counts(
            count, self.cfg.command_hold_s
        )
        if immediate:
            self._commands[env_ids] = targets

    def _apply_smooth_commands(self) -> None:
        self._command_steps_remaining -= 1
        due = torch.nonzero(
            self._command_steps_remaining <= 0, as_tuple=False
        ).squeeze(-1)
        if len(due):
            self._sample_command_targets(due, immediate=False)

        alpha = min(
            1.0,
            self.step_dt / max(self.cfg.command_smoothing_time_s, self.step_dt),
        )
        self._commands += alpha * (self._command_targets - self._commands)

    def _apply_random_pushes(self) -> None:
        if self.cfg.push_probability <= 0.0:
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
            torch.rand(len(due), device=self.device)
            < self.cfg.push_probability
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
            -self.cfg.push_linear_velocity,
            self.cfg.push_linear_velocity,
        )
        root_velocity[:, 5] += torch.empty(
            len(selected), device=self.device
        ).uniform_(
            -self.cfg.push_yaw_velocity,
            self.cfg.push_yaw_velocity,
        )
        self._robot.write_root_velocity_to_sim_index(
            root_velocity=root_velocity, env_ids=selected
        )

    def _pre_physics_step(self, actions: torch.Tensor):
        self._previous_actions = self._actions.clone()
        self._apply_smooth_commands()
        self._apply_random_pushes()
        super()._pre_physics_step(actions)

    def _get_observations(self) -> dict:
        angular_velocity = self._robot.data.root_ang_vel_b.torch.clone()
        projected_gravity = self._robot.data.projected_gravity_b.torch.clone()
        joint_position = (
            self._robot.data.joint_pos.torch
            - self._robot.data.default_joint_pos.torch
        )
        joint_velocity = self._robot.data.joint_vel.torch.clone()

        if self.cfg.observation_noise_enabled:
            angular_velocity += torch.empty_like(angular_velocity).uniform_(
                -self.cfg.gyro_noise, self.cfg.gyro_noise
            )
            projected_gravity += torch.empty_like(projected_gravity).uniform_(
                -self.cfg.gravity_noise, self.cfg.gravity_noise
            )
            joint_position += torch.empty_like(joint_position).uniform_(
                -self.cfg.joint_position_noise,
                self.cfg.joint_position_noise,
            )
            joint_velocity += torch.empty_like(joint_velocity).uniform_(
                -self.cfg.joint_velocity_noise,
                self.cfg.joint_velocity_noise,
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
                "V2 observation frame mismatch: "
                f"expected {self.cfg.observation_frame_size}, "
                f"received {frame.shape[1]}"
            )

        self._observation_history = torch.roll(
            self._observation_history, shifts=-1, dims=1
        )
        self._observation_history[:, -1] = frame
        fresh = ~self._history_ready
        if torch.any(fresh):
            self._observation_history[fresh] = frame[fresh].unsqueeze(1).expand(
                -1, self.cfg.observation_history_length, -1
            )
            self._history_ready[fresh] = True

        return {"policy": self._observation_history.flatten(start_dim=1)}

    def _dense_diagonal_gait_reward(
        self,
        current_air_time: torch.Tensor,
        current_contact_time: torch.Tensor,
    ) -> torch.Tensor:
        """Dense Spot-style diagonal pair timing without a sparse product."""

        def similarity(left: torch.Tensor, right: torch.Tensor) -> torch.Tensor:
            return torch.exp(
                -torch.square(left - right) / self.cfg.diagonal_gait_std
            )

        # Foot order is FR, FL, BR, BL. Diagonal pairs are FR+BL and FL+BR.
        synchronization = torch.stack(
            (
                similarity(current_air_time[:, 0], current_air_time[:, 3]),
                similarity(
                    current_contact_time[:, 0], current_contact_time[:, 3]
                ),
                similarity(current_air_time[:, 1], current_air_time[:, 2]),
                similarity(
                    current_contact_time[:, 1], current_contact_time[:, 2]
                ),
            ),
            dim=1,
        ).mean(dim=1)
        opposition = torch.stack(
            (
                similarity(current_air_time[:, 0], current_contact_time[:, 1]),
                similarity(current_contact_time[:, 0], current_air_time[:, 1]),
                similarity(current_air_time[:, 3], current_contact_time[:, 2]),
                similarity(current_contact_time[:, 3], current_air_time[:, 2]),
            ),
            dim=1,
        ).mean(dim=1)
        return 0.5 * (synchronization + opposition)

    def _get_rewards(self) -> torch.Tensor:
        (
            body_forward,
            body_lateral,
            world_forward,
            _,
            heading_alignment,
            _,
        ) = self._get_physical_motion()
        root_lin_vel_b = self._robot.data.root_lin_vel_b.torch
        root_ang_vel_b = self._robot.data.root_ang_vel_b.torch
        projected_gravity = self._robot.data.projected_gravity_b.torch
        requested_forward = self._commands[:, 0]
        moving_command = requested_forward > 0.05
        command_forward = requested_forward.clamp_min(0.05)

        planar_velocity_error = torch.sqrt(
            torch.square(command_forward - body_forward)
            + torch.square(body_lateral)
        )
        tracking_quality = torch.exp(
            -planar_velocity_error / self.cfg.velocity_tracking_std
        )
        standing_quality = torch.exp(
            -command_forward / self.cfg.velocity_tracking_std
        )
        centered_tracking = (
            (tracking_quality - standing_quality)
            / (1.0 - standing_quality).clamp_min(0.10)
        )
        signed_progress = torch.clamp(
            body_forward / command_forward, -1.0, 1.0
        )
        moving_locomotion = 0.5 * (centered_tracking + signed_progress)
        stationary_locomotion = torch.exp(
            -torch.sqrt(
                torch.square(body_forward) + torch.square(body_lateral)
            )
            / self.cfg.velocity_tracking_std
        )
        locomotion = torch.where(
            moving_command, moving_locomotion, stationary_locomotion
        )

        yaw_error = torch.abs(self._commands[:, 2] - root_ang_vel_b[:, 2])
        yaw_tracking = torch.exp(
            -yaw_error / self.cfg.yaw_tracking_std
        )

        contact_history = self._contact_sensor.data.net_forces_w_history.torch
        base_contact = torch.any(
            torch.max(
                torch.linalg.vector_norm(
                    contact_history[:, :, self._base_sensor_ids], dim=-1
                ),
                dim=1,
            )[0]
            > 1.0,
            dim=1,
        )
        root_height = (
            self._robot.data.root_pos_w.torch[:, 2]
            - self._terrain.env_origins[:, 2]
        )
        fell = (
            base_contact
            | (root_height < self.cfg.termination_height)
            | (
                projected_gravity[:, 2]
                > self.cfg.termination_projected_gravity_z
            )
        )

        current_air_time = self._contact_sensor.data.current_air_time.torch[
            :, self._feet_sensor_ids
        ]
        current_contact_time = (
            self._contact_sensor.data.current_contact_time.torch[
                :, self._feet_sensor_ids
            ]
        )
        diagonal_gait = self._dense_diagonal_gait_reward(
            current_air_time, current_contact_time
        ) * torch.clamp(moving_locomotion, 0.0, 1.0) * moving_command
        yaw_tracking *= torch.clamp(locomotion, 0.0, 1.0)

        feet_contact = (
            torch.max(
                torch.linalg.vector_norm(
                    contact_history[:, :, self._feet_sensor_ids], dim=-1
                ),
                dim=1,
            )[0]
            > 1.0
        )
        feet_velocity_xy = self._robot.data.body_lin_vel_w.torch[
            :, self._feet_body_ids, :2
        ]
        foot_slip = torch.sum(
            torch.linalg.vector_norm(feet_velocity_xy, dim=-1)
            * feet_contact,
            dim=1,
        )
        undesired_contact = torch.sum(
            (
                torch.max(
                    torch.linalg.vector_norm(
                        contact_history[
                            :, :, self._undesired_contact_sensor_ids
                        ],
                        dim=-1,
                    ),
                    dim=1,
                )[0]
                > 1.0
            ).float(),
            dim=1,
        )
        stability = (
            torch.sum(torch.square(projected_gravity[:, :2]), dim=1)
            + 0.25 * torch.square(root_lin_vel_b[:, 2])
            + 0.10 * torch.sum(
                torch.square(root_ang_vel_b[:, :2]), dim=1
            )
        )
        action_rate = torch.sum(
            torch.square(self._actions - self._previous_actions), dim=1
        )

        terms = {
            "locomotion": (
                locomotion * self.cfg.locomotion_reward_scale * self.step_dt
            ),
            "track_yaw_rate": (
                yaw_tracking * self.cfg.yaw_reward_scale * self.step_dt
            ),
            "diagonal_gait": (
                diagonal_gait
                * self.cfg.diagonal_gait_reward_scale
                * self.step_dt
            ),
            "stability": (
                stability * self.cfg.stability_penalty_scale * self.step_dt
            ),
            "action_rate": (
                action_rate * self.cfg.action_rate_penalty_scale * self.step_dt
            ),
            "foot_slip": (
                foot_slip
                * self.cfg.foot_slip_penalty_scale_v2
                * self.step_dt
            ),
            "undesired_contact": (
                undesired_contact
                * self.cfg.undesired_contact_penalty_scale_v2
                * self.step_dt
            ),
            "fall": fell.float() * self.cfg.fall_penalty_scale_v2,
        }

        for key, value in terms.items():
            self._episode_sums[key] += value
        self._survival_steps += 1.0
        self._velocity_error_sum += torch.sqrt(
            torch.square(requested_forward - body_forward)
            + torch.square(body_lateral)
        )
        self._world_forward_speed_sum += body_forward
        self._body_lateral_speed_sum += torch.abs(body_lateral)
        self._heading_error_sum += yaw_error
        self._foot_swing_steps += (~feet_contact).float()
        first_contact = self._contact_sensor.compute_first_contact(
            self.step_dt
        ).torch[:, self._feet_sensor_ids]
        self._foot_landings += first_contact.float()

        if self.cfg.print_play_metrics:
            self._play_step_count += 1
            root_xy = self._robot.data.root_pos_w.torch[0, :2]
            if self._play_start_xy is None:
                self._play_start_xy = root_xy.clone()
            if self._play_step_count % 50 == 0:
                displacement_xy = root_xy - self._play_start_xy
                swing_fraction = self._foot_swing_steps[0] / max(
                    float(self.episode_length_buf[0].item()), 1.0
                )
                print(
                    "PLAY_METRICS "
                    f"step={self._play_step_count} "
                    f"command_forward={self._commands[0, 0].item():.4f} "
                    f"command_yaw={self._commands[0, 2].item():.4f} "
                    f"body_forward={body_forward[0].item():.4f} "
                    f"body_lateral={body_lateral[0].item():.4f} "
                    f"world_forward={world_forward[0].item():.4f} "
                    f"forward_displacement={-displacement_xy[1].item():.4f} "
                    f"lateral_displacement={displacement_xy[0].item():.4f} "
                    f"heading_alignment={heading_alignment[0].item():.4f} "
                    f"swing_fraction_frflbrbl="
                    f"{','.join(f'{value:.3f}' for value in swing_fraction.tolist())} "
                    f"diagonal_gait={diagonal_gait[0].item():.4f} "
                    f"foot_slip={foot_slip[0].item():.4f} "
                    f"height={root_height[0].item():.4f}",
                    flush=True,
                )

        return torch.stack(tuple(terms.values())).sum(dim=0)

    def _reset_idx(self, env_ids: torch.Tensor | None):
        if env_ids is None:
            env_ids = torch.arange(
                self.num_envs, dtype=torch.long, device=self.device
            )
        super()._reset_idx(env_ids)
        log = self.extras.get("log", {})
        if "Metrics/mean_world_forward_speed" in log:
            log["Metrics/mean_body_forward_speed"] = log.pop(
                "Metrics/mean_world_forward_speed"
            )
        if "Metrics/mean_heading_error" in log:
            log["Metrics/mean_yaw_rate_error"] = log.pop(
                "Metrics/mean_heading_error"
            )

        self._sample_command_targets(env_ids, immediate=True)
        self._push_steps_remaining[env_ids] = self._random_step_counts(
            len(env_ids), self.cfg.push_interval_s
        )
        self._history_ready[env_ids] = False

        count = len(env_ids)
        use_large_tilt = (
            torch.rand(count, device=self.device)
            < self.cfg.reset_large_tilt_fraction
        )
        tilt_limit = torch.where(
            use_large_tilt,
            torch.full(
                (count,),
                math.radians(self.cfg.reset_large_tilt_deg),
                device=self.device,
            ),
            torch.full(
                (count,),
                math.radians(self.cfg.reset_small_tilt_deg),
                device=self.device,
            ),
        )
        tilt_direction = torch.empty(count, device=self.device).uniform_(
            0.0, 2.0 * math.pi
        )
        tilt_magnitude = torch.rand(count, device=self.device) * tilt_limit
        roll = tilt_magnitude * torch.cos(tilt_direction)
        pitch = tilt_magnitude * torch.sin(tilt_direction)
        if self.cfg.randomize_reset_yaw:
            yaw = torch.empty(count, device=self.device).uniform_(
                -math.pi, math.pi
            )
        else:
            yaw = torch.zeros(count, device=self.device)

        root_pose = self._robot.data.default_root_pose.torch[env_ids].clone()
        root_pose[:, :3] += self._terrain.env_origins[env_ids]
        root_pose[:, 3:7] = quat_from_euler_xyz(roll, pitch, yaw)
        root_velocity = self._robot.data.default_root_vel.torch[env_ids].clone()
        self._robot.write_root_pose_to_sim_index(
            root_pose=root_pose, env_ids=env_ids
        )
        self._robot.write_root_velocity_to_sim_index(
            root_velocity=root_velocity, env_ids=env_ids
        )

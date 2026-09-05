"""Delivery experiment with measured servo trajectories and sampled sensors."""
import math

import torch
from isaaclab.utils.math import quat_apply_inverse
from simple_dog_task_current_body_v4.simple_dog_current_body_v4_env import SimpleDogCurrentBodyV4Env
from deployable_dynamics import ServoTrajectory, gravity_estimate, motor_to_policy, policy_to_motor
from .env_cfg import SERVO_FIT


class DeliveryEnv(SimpleDogCurrentBodyV4Env):
    def __init__(self, cfg, render_mode=None, **kwargs):
        super().__init__(cfg, render_mode, **kwargs)
        self._servo_trajectory = ServoTrajectory(SERVO_FIT, self.num_envs, self.device, self.step_dt)
        self._encoder_previous = torch.zeros(self.num_envs, 12, device=self.device)
        self._gravity_previous = torch.zeros(self.num_envs, 3, device=self.device)
        self._world_velocity_previous = self._robot.data.root_lin_vel_w.torch.clone()
        self._sensor_ready = torch.zeros(self.num_envs, dtype=torch.bool, device=self.device)
        self._encoder_zeros = torch.tensor([
            j["zero_deg"] * math.pi / 180
            for j in sorted(SERVO_FIT["calibration"]["joints"], key=lambda j: j["policy_index"])
        ], device=self.device)
        self._encoder_signs = torch.tensor([
            1. if j["servo_degrees_per_policy_radian"] > 0 else -1.
            for j in sorted(SERVO_FIT["calibration"]["joints"], key=lambda j: j["policy_index"])
        ], device=self.device)
        ids = torch.arange(self.num_envs, device=self.device)
        q, _ = self._get_policy_joint_state()
        self._servo_trajectory.reset(ids, q, cfg.domain_randomization_enabled)
        self._episode_sums["opposite_leg_sync"] = torch.zeros(self.num_envs, device=self.device)

    def _place_resets_on_scheduled_terrain(self, env_ids):
        # Fixed mild distribution for this experiment. A continuation changes
        # terrain only after measured acceptance, never because time passed.
        pass

    def _pre_physics_step(self, actions):
        # Reuse only the reviewed command/filter/slew/reset handling. This
        # family's cfg disables the old lag, residual and measured-p95 cap.
        super()._pre_physics_step(actions)
        q_target = (self._processed_actions - self._robot.data.default_joint_pos.torch[:, self._policy_joint_ids])
        self._servo_trajectory.command(q_target * self._joint_directions)

    def _apply_action(self):
        logical = self._servo_trajectory.step(self.physics_dt)
        self._processed_actions = (self._robot.data.default_joint_pos.torch[:, self._policy_joint_ids]
                                   + logical * self._joint_directions)
        super()._apply_action()

    def _get_observations(self):
        data = self._robot.data
        fresh = ~self._sensor_ready
        q, _ = self._get_policy_joint_state()
        encoder = self._encoder_zeros + self._encoder_signs * policy_to_motor(q)
        if self.cfg.observation_noise_enabled:
            encoder = encoder + torch.empty_like(encoder).uniform_(-self.cfg.joint_position_noise, self.cfg.joint_position_noise)
        encoder = torch.round(encoder * (4096 / (2 * math.pi))) * (2 * math.pi / 4096)
        position = motor_to_policy((encoder - self._encoder_zeros) * self._encoder_signs)
        velocity = torch.where(fresh[:, None], 0., (position - self._encoder_previous) / self.step_dt)
        self._encoder_previous.copy_(position)

        gyro = self._semantic_vector_b(data.root_ang_vel_b.torch).clone()
        # Specific force at the body origin, with gravity removed. Finite
        # differences include contact acceleration instead of supplying the
        # actor an unrealistically perfect simulator gravity vector.
        world_velocity = data.root_lin_vel_w.torch
        acceleration_w = (world_velocity - self._world_velocity_previous) / self.step_dt
        acceleration_w[fresh] = 0.
        acceleration_w[:, 2] += 9.81
        acceleration = self._semantic_vector_b(quat_apply_inverse(data.root_quat_w.torch, acceleration_w)) * (1000 / 9.81)
        self._world_velocity_previous.copy_(world_velocity)
        if self.cfg.observation_noise_enabled:
            gyro += torch.empty_like(gyro).uniform_(-self.cfg.gyro_noise, self.cfg.gyro_noise)
            acceleration += torch.empty_like(acceleration).uniform_(-self.cfg.accelerometer_noise_mg, self.cfg.accelerometer_noise_mg)
        gravity = gravity_estimate(self._gravity_previous, acceleration, gyro, self.step_dt, fresh)
        self._gravity_previous.copy_(gravity)
        self._sensor_ready[:] = True
        frame = torch.cat((gyro, gravity, self._commands, position, .05 * velocity, self._actions), dim=-1)
        self._observation_history = torch.roll(self._observation_history, -1, dims=1)
        self._observation_history[:, -1] = frame
        self._observation_history[fresh] = frame[fresh, None, :]
        self._history_ready[:] = True
        current, valid = self._simulate_current()
        current_frame = torch.cat((current, valid), dim=-1)
        self._current_history = torch.roll(self._current_history, -1, dims=1)
        self._current_history[:, -1] = current_frame
        self._current_history[fresh] = current_frame[fresh, None, :]
        self._current_history_ready[:] = True
        # Every sampled observation here is actually 20 ms apart. Delayed
        # actuation is modeled causally; it does not falsify this timestamp.
        history = torch.cat((self._observation_history, self._current_history, self._timing_history), dim=-1)
        observation = torch.cat((history[:, self.cfg.selected_history_indices].flatten(1),
                                 self._commands, self._posture_commands), dim=-1)
        self._update_video_camera()
        return {"policy": observation}

    def _get_rewards(self):
        motion = self._semantic_vector_b(self._robot.data.root_lin_vel_b.torch)
        gyro = self._semantic_vector_b(self._robot.data.root_ang_vel_b.torch)
        height, roll, pitch = self._body_posture()
        planar_error = torch.linalg.vector_norm(motion[:, :2] - self._commands[:, :2], dim=-1)
        yaw_error = (gyro[:, 2] - self._commands[:, 2]).abs()
        height_error = height - self.cfg.nominal_support_height_m - self._posture_commands[:, 0]
        attitude_error = (roll - self._posture_commands[:, 1]).square() + (pitch - self._posture_commands[:, 2]).square()
        tracking = (3. * torch.exp(-planar_error.square() / .01)
                    + torch.exp(-yaw_error.square() / .09)
                    + .5 * torch.exp(-height_error.square() / .0004)
                    + .5 * torch.exp(-attitude_error / .02))
        speed = torch.linalg.vector_norm(self._commands[:, :2], dim=-1)
        aligned = (motion[:, :2] * self._commands[:, :2]).sum(-1) / speed.clamp_min(1e-6)
        planar_progress = (aligned / speed.clamp_min(.03)).clamp(-1, 1)
        yaw_progress = (gyro[:, 2] * self._commands[:, 2].sign() / self._commands[:, 2].abs().clamp_min(.05)).clamp(-1, 1)
        linear_active = speed > .03
        yaw_active = self._commands[:, 2].abs() > .05
        active_count = linear_active.float() + yaw_active.float()
        progress = (planar_progress * linear_active + yaw_progress * yaw_active) / active_count.clamp_min(1)
        shortfall = (active_count > 0) * (1 - progress.clamp(0, 1))
        delta = self._actions - self._previous_actions
        regularization = .02 * delta.square().sum(-1)
        contact = self._contact_sensor.data
        sync = self._dense_diagonal_gait_reward(contact.current_air_time.torch[:, self._feet_sensor_ids],
                                               contact.current_contact_time.torch[:, self._feet_sensor_ids])
        sync_rate = self.cfg.opposite_leg_sync_reward_scale * progress.clamp(0, 1) * sync
        rate = tracking + .5 * progress - .5 * shortfall - regularization + sync_rate
        active = ~self._reset_hold_active_mask
        reward = torch.where(active, rate * self.step_dt, 0.)
        # A fall cannot avoid future tracking costs by ending the episode.
        reward -= 5. * (self.reset_terminated & active)
        self._survival_steps += 1.
        self._velocity_error_sum += active * planar_error
        self._world_forward_speed_sum += active * motion[:, 0]
        self._body_lateral_speed_sum += active * motion[:, 1].abs()
        self._heading_error_sum += active * yaw_error
        self._terrain_commanded_distance += active * speed * self.step_dt
        self._terrain_tracked_distance += active * torch.minimum(aligned, speed).clamp_min(0) * self.step_dt
        self._episode_sums["body_tracking"] += active * tracking * self.step_dt
        self._episode_sums["body_motion_shortfall"] -= active * .5 * shortfall * self.step_dt
        self._episode_sums["opposite_leg_sync"] += active * sync_rate * self.step_dt
        return reward

    def _simulate_current(self):
        previous = self._last_finite_current.clone()
        current, valid = super()._simulate_current()
        if self._evaluation_segments:
            elapsed = self._play_step_count
            for segment in self._evaluation_segments:
                if elapsed < segment[1]:
                    if segment[0] == "current_dropout_walk" and elapsed % 5 == 0:
                        current = previous
                        valid = torch.zeros_like(valid)
                        self._last_finite_current = previous
                        self._latest_normalized_current = current
                        self._latest_current_validity = valid
                    break
                elapsed -= segment[1]
        return current, valid

    def _reset_idx(self, env_ids):
        super()._reset_idx(env_ids)
        if hasattr(self, "_servo_trajectory"):
            if env_ids is None:
                env_ids = torch.arange(self.num_envs, device=self.device)
            q, _ = self._get_policy_joint_state()
            self._servo_trajectory.reset(env_ids, q[env_ids], self.cfg.domain_randomization_enabled)
            self._sensor_ready[env_ids] = False
            self._world_velocity_previous[env_ids] = self._robot.data.root_lin_vel_w.torch[env_ids]

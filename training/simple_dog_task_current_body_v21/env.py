"""Learn small feedback corrections around a persistent physical stride."""
import math
import json
from pathlib import Path

import torch

from delivery_gait import combine_stride
from simple_dog_task_current_body_v20.env import DeliveryEnv
from simple_dog_task_v2.simple_dog_v2_env import SimpleDogV2Env


class StrideEnv(DeliveryEnv):
    def __init__(self, cfg, render_mode=None, **kwargs):
        self._stride_spec = json.loads((Path(__file__).resolve().parents[1] / "fits" /
                                       cfg.stride_reference_filename).read_text())
        super().__init__(cfg, render_mode, **kwargs)
        self._stride_elapsed = torch.zeros(self.num_envs, device=self.device)

    def _apply_smooth_commands(self):
        # Acquisition intentionally has exactly one easy command. Later
        # stages must use explicit configurations and matched evaluation.
        self._commands[:] = torch.as_tensor(self.cfg.stride_command, device=self.device)
        self._command_targets.copy_(self._commands)
        self._posture_commands.zero_()
        self._posture_targets.zero_()

    def _pre_physics_step(self, residual):
        targets = combine_stride(residual, self._commands, self._posture_commands,
                                 self._gravity_previous, self._stride_elapsed, self._stride_spec)
        super()._pre_physics_step(targets)
        self._stride_elapsed += self.step_dt

    def _get_observations(self):
        observation = super()._get_observations()["policy"]
        active_time = (self._stride_elapsed - self._stride_spec["settle_seconds"]).clamp_min(0.)
        angle = 2 * math.pi * self._stride_spec["frequency_hz"] * active_time
        clock = torch.stack((torch.sin(angle), torch.cos(angle)), dim=-1)
        observation = torch.cat((observation, clock), dim=-1)
        return {"policy": observation, "critic": self._critic_observation(observation)}

    def _reset_idx(self, env_ids):
        super()._reset_idx(env_ids)
        if env_ids is None:
            env_ids = torch.arange(self.num_envs, device=self.device)
        if hasattr(self, "_stride_elapsed"):
            self._stride_elapsed[env_ids] = 0.
        if hasattr(self, "_commands"):
            self._commands[env_ids] = torch.as_tensor(self.cfg.stride_command, device=self.device)
            self._command_targets[env_ids] = self._commands[env_ids]
            self._posture_commands[env_ids] = 0.
            self._posture_targets[env_ids] = 0.


class StrideCommandEnv(StrideEnv):
    def _sample_posture_targets(self, env_ids, *, immediate):
        self._posture_targets[env_ids] = 0.
        if immediate:
            self._posture_commands[env_ids] = 0.

    def _apply_posture_commands(self):
        # The old independent posture timer must not affect rewards between
        # the action and observation, even though the actor sees neutral pose.
        self._posture_targets.zero_()
        self._posture_commands.zero_()

    def _sample_command_targets(self, env_ids, *, immediate):
        count = len(env_ids)
        if immediate:
            self._command_targets[env_ids] = torch.tensor(self.cfg.stride_command, device=self.device)
            self._commands[env_ids] = self._command_targets[env_ids]
            # Reset itself produces the first observation at elapsed zero.
            self._command_steps_remaining[env_ids] = round(self.cfg.stride_initial_forward_s / self.step_dt) + 1
        else:
            menu = torch.tensor(self.cfg.stride_command_menu, device=self.device)
            if self.cfg.stride_evaluate_commands:
                choice = env_ids % len(menu)
                self._command_steps_remaining[env_ids] = 1_000_000
            else:
                choice = torch.randint(len(menu), (count,), device=self.device)
                self._command_steps_remaining[env_ids] = self._random_step_counts(count, self.cfg.stride_command_hold_s)
            self._command_targets[env_ids] = menu[choice]
        if hasattr(self, "_posture_targets"):
            self._posture_targets[env_ids] = 0.
            self._posture_commands[env_ids] = 0.

    def _apply_smooth_commands(self):
        # Commands have already advanced before the actor observation, as on
        # Pixel. The inherited pre-physics call must not smooth them twice.
        pass

    def _get_observations(self):
        SimpleDogV2Env._apply_smooth_commands(self)
        self._posture_targets.zero_()
        self._posture_commands.zero_()
        return super()._get_observations()

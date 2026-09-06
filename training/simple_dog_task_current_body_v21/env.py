"""Learn small feedback corrections around a persistent physical stride."""
import math

import torch

from delivery_gait import combine_stride
from simple_dog_task_current_body_v20.env import DeliveryEnv
from .env_cfg import STRIDE_SPEC


class StrideEnv(DeliveryEnv):
    def __init__(self, cfg, render_mode=None, **kwargs):
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
                                 self._gravity_previous, self._stride_elapsed, STRIDE_SPEC)
        super()._pre_physics_step(targets)
        self._stride_elapsed += self.step_dt

    def _get_observations(self):
        observation = super()._get_observations()["policy"]
        active_time = (self._stride_elapsed - STRIDE_SPEC["settle_seconds"]).clamp_min(0.)
        angle = 2 * math.pi * STRIDE_SPEC["frequency_hz"] * active_time
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

"""Stride acquisition, isolated from direct-action V20 checkpoints."""
import gymnasium as gym

gym.register(
    id="Isaac-Locomotion-CurrentBodyV21-Acquire-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.env:StrideEnv",
    disable_env_checker=True,
    kwargs={"env_cfg_entry_point": f"{__name__}.env_cfg:StrideAcquireCfg",
            "rl_games_cfg_entry_point": f"{__name__}.agents:rl_games_ppo_cfg.yaml"},
)

gym.register(
    id="Isaac-Locomotion-CurrentBodyV21-Commands-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.env:StrideCommandEnv",
    disable_env_checker=True,
    kwargs={"env_cfg_entry_point": f"{__name__}.env_cfg:StrideCommandsCfg",
            "rl_games_cfg_entry_point": f"{__name__}.agents:rl_games_ppo_cfg.yaml"},
)

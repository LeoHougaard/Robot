"""Profile-driven Version 2 quadruped locomotion tasks."""

import gymnasium as gym

from . import agents


gym.register(
    id="Isaac-Locomotion-V2-Core-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2CoreEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Robust-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2RobustEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2GoalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2RoughEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Simple-Dog-Direct-Play-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2PlayEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Core-Simple-Dog-Direct-Eval-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2CoreEvalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-Eval-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2GoalEvalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Robust-Simple-Dog-Direct-Eval-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2RobustEvalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-Play-v0",
    entry_point=f"{__name__}.simple_dog_v2_env:SimpleDogV2Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_v2_env_cfg:SimpleDogV2RoughPlayEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

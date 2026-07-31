"""Isaac Lab task registration for Leo's eight-joint Onshape dog."""

import gymnasium as gym

from . import agents


gym.register(
    id="Isaac-Velocity-Flat-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_env:SimpleDogEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.simple_dog_env_cfg:SimpleDogFlatEnvCfg",
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0",
    entry_point=f"{__name__}.simple_dog_env:SimpleDogEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.simple_dog_env_cfg:SimpleDogFlatPlayEnvCfg",
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Velocity-Rough-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_env:SimpleDogEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.simple_dog_env_cfg:SimpleDogRoughEnvCfg",
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_rough_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Velocity-Rough-NoScan-Simple-Dog-Diagnostic-v0",
    entry_point=f"{__name__}.simple_dog_env:SimpleDogEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_env_cfg:"
            "SimpleDogRoughNoScanDiagnosticEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_rough_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Velocity-Rough-Simple-Dog-Direct-Play-v0",
    entry_point=f"{__name__}.simple_dog_env:SimpleDogEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.simple_dog_env_cfg:SimpleDogRoughPlayEnvCfg",
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_rough_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Velocity-Rough-Simple-Dog-Direct-Validation-v0",
    entry_point=f"{__name__}.simple_dog_env:SimpleDogEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_env_cfg:SimpleDogRoughValidationEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_rough_ppo_cfg.yaml",
    },
)

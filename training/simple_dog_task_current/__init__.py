"""Register the separate CurrentV3 quadruped policy family."""

import gymnasium as gym

from . import agents


gym.register(
    id="Isaac-Locomotion-CurrentV3-Core-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3CoreEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)


gym.register(
    id="Isaac-Locomotion-CurrentV3-Reverse-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3ReverseEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)


gym.register(
    id="Isaac-Locomotion-CurrentV3-Strafe-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3StrafeEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)


gym.register(
    id="Isaac-Locomotion-CurrentV3-Turn-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3TurnEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)


gym.register(
    id="Isaac-Locomotion-CurrentV3-Goal-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3GoalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)


gym.register(
    id="Isaac-Locomotion-CurrentV3-Posture-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3PostureEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)


gym.register(
    id="Isaac-Locomotion-CurrentV3-Rough-Simple-Dog-Direct-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3RoughPolicyEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-CurrentV3-Simple-Dog-Direct-Eval-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3EvalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-CurrentV3-Simple-Dog-Direct-Play-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3PlayEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-CurrentV3-Flat-Simple-Dog-Direct-Eval-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3FlatEvalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

gym.register(
    id="Isaac-Locomotion-CurrentV3-Stress-Simple-Dog-Direct-Eval-v0",
    entry_point=f"{__name__}.simple_dog_current_env:SimpleDogCurrentV3Env",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": (
            f"{__name__}.simple_dog_current_env_cfg:SimpleDogCurrentV3StressEvalEnvCfg"
        ),
        "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
    },
)

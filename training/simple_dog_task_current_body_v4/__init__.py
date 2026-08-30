"""Register the separate CurrentBodyV4 mixed-command policy family."""

import gymnasium as gym

from . import agents


def _register(task_id: str, cfg: str) -> None:
    gym.register(
        id=task_id,
        entry_point=(
            f"{__name__}.simple_dog_current_body_v4_env:"
            "SimpleDogCurrentBodyV4Env"
        ),
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": (
                f"{__name__}.simple_dog_current_body_v4_env_cfg:{cfg}"
            ),
            "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
        },
    )


_register(
    "Isaac-Locomotion-CurrentBodyV4-Hard-Simple-Dog-Direct-v0",
    "SimpleDogCurrentBodyV4HardEnvCfg",
)
_register(
    "Isaac-Locomotion-CurrentBodyV4-Simple-Dog-Direct-Eval-v0",
    "SimpleDogCurrentBodyV4EvalEnvCfg",
)
_register(
    "Isaac-Locomotion-CurrentBodyV4-Simple-Dog-Direct-Play-v0",
    "SimpleDogCurrentBodyV4PlayEnvCfg",
)

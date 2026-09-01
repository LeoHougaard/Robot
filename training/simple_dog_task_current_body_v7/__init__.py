"""Register the distance-persistent CurrentBodyV7 policy family."""

import gymnasium as gym

from . import agents


def _register(task_id: str, cfg: str) -> None:
    gym.register(
        id=task_id,
        entry_point=f"{__name__}.simple_dog_current_body_v7_env:SimpleDogCurrentBodyV7Env",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": f"{__name__}.simple_dog_current_body_v7_env_cfg:{cfg}",
            "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
        },
    )


_register("Isaac-Locomotion-CurrentBodyV7-Hard-Simple-Dog-Direct-v0", "SimpleDogCurrentBodyV7HardEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV7-Simple-Dog-Direct-Eval-v0", "SimpleDogCurrentBodyV7EvalEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV7-Simple-Dog-Direct-Play-v0", "SimpleDogCurrentBodyV7PlayEnvCfg")
_register(
    "Isaac-Locomotion-CurrentBodyV7-Simple-Dog-Direct-Push-Eval-v0",
    "SimpleDogCurrentBodyV7PushEvalEnvCfg",
)

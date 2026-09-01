"""Register the isolated-translation CurrentBodyV8 policy family."""

import gymnasium as gym

from . import agents


def _register(task_id: str, cfg: str) -> None:
    gym.register(
        id=task_id,
        entry_point=f"{__name__}.simple_dog_current_body_v8_env:SimpleDogCurrentBodyV8Env",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": f"{__name__}.simple_dog_current_body_v8_env_cfg:{cfg}",
            "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
        },
    )


_register("Isaac-Locomotion-CurrentBodyV8-Hard-Simple-Dog-Direct-v0", "SimpleDogCurrentBodyV8HardEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV8-Simple-Dog-Direct-Eval-v0", "SimpleDogCurrentBodyV8EvalEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV8-Simple-Dog-Direct-Play-v0", "SimpleDogCurrentBodyV8PlayEnvCfg")
_register(
    "Isaac-Locomotion-CurrentBodyV8-Simple-Dog-Direct-Push-Eval-v0",
    "SimpleDogCurrentBodyV8PushEvalEnvCfg",
)

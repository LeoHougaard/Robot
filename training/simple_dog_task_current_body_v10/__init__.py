"""Register the translation-bootstrap CurrentBodyV10 policy family."""

import gymnasium as gym

from . import agents


def _register(task_id: str, cfg: str) -> None:
    gym.register(
        id=task_id,
        entry_point=f"{__name__}.simple_dog_current_body_v10_env:SimpleDogCurrentBodyV10Env",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": f"{__name__}.simple_dog_current_body_v10_env_cfg:{cfg}",
            "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
        },
    )


_register("Isaac-Locomotion-CurrentBodyV10-Hard-Simple-Dog-Direct-v0", "SimpleDogCurrentBodyV10HardEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV10-Simple-Dog-Direct-Eval-v0", "SimpleDogCurrentBodyV10EvalEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV10-Simple-Dog-Direct-Play-v0", "SimpleDogCurrentBodyV10PlayEnvCfg")
_register(
    "Isaac-Locomotion-CurrentBodyV10-Simple-Dog-Direct-Push-Eval-v0",
    "SimpleDogCurrentBodyV10PushEvalEnvCfg",
)

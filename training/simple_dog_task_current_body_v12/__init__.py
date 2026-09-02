"""Register the exploratory staged-terrain CurrentBodyV12 policy family."""

import gymnasium as gym

from . import agents


def _register(task_id: str, cfg: str) -> None:
    gym.register(
        id=task_id,
        entry_point=f"{__name__}.simple_dog_current_body_v12_env:SimpleDogCurrentBodyV12Env",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": f"{__name__}.simple_dog_current_body_v12_env_cfg:{cfg}",
            "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
        },
    )


_register("Isaac-Locomotion-CurrentBodyV12-Hard-Simple-Dog-Direct-v0", "SimpleDogCurrentBodyV12HardEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV12-Simple-Dog-Direct-Eval-v0", "SimpleDogCurrentBodyV12EvalEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV12-Simple-Dog-Direct-Play-v0", "SimpleDogCurrentBodyV12PlayEnvCfg")
_register(
    "Isaac-Locomotion-CurrentBodyV12-Simple-Dog-Direct-Push-Eval-v0",
    "SimpleDogCurrentBodyV12PushEvalEnvCfg",
)

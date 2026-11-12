"""Register the progress-gated opposite-leg CurrentBodyV18 family."""

import gymnasium as gym

from . import agents


def _register(task_id: str, cfg: str) -> None:
    gym.register(
        id=task_id,
        entry_point=f"{__name__}.simple_dog_current_body_v18_env:SimpleDogCurrentBodyV18Env",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": f"{__name__}.simple_dog_current_body_v18_env_cfg:{cfg}",
            "rl_games_cfg_entry_point": f"{agents.__name__}:rl_games_ppo_cfg.yaml",
        },
    )


_register("Isaac-Locomotion-CurrentBodyV18-Hard-Simple-Dog-Direct-v0", "SimpleDogCurrentBodyV18HardEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV18-Simple-Dog-Direct-Eval-v0", "SimpleDogCurrentBodyV18EvalEnvCfg")
_register("Isaac-Locomotion-CurrentBodyV18-Simple-Dog-Direct-Play-v0", "SimpleDogCurrentBodyV18PlayEnvCfg")
_register(
    "Isaac-Locomotion-CurrentBodyV18-Simple-Dog-Direct-Push-Eval-v0",
    "SimpleDogCurrentBodyV18PushEvalEnvCfg",
)

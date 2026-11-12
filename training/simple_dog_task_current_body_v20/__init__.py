"""Register the measured-actuator delivery experiment, separate from V1–V19."""
import gymnasium as gym

for suffix, cfg in (("Train", "DeliveryTrainCfg"), ("Eval", "DeliveryEvalCfg"),
                    ("Flat-Eval", "DeliveryFlatEvalCfg"), ("Stress-Eval", "DeliveryStressEvalCfg")):
    gym.register(
        id=f"Isaac-Locomotion-CurrentBodyV20-{suffix}-Simple-Dog-Direct-v0",
        entry_point=f"{__name__}.env:DeliveryEnv",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": f"{__name__}.env_cfg:{cfg}",
            "rl_games_cfg_entry_point": f"{__name__}.agents:rl_games_ppo_cfg.yaml",
        },
    )

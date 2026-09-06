"""The detailed closed linkage already contains the knee transmission."""
import gymnasium as gym

for stage, env, cfg in (("Acquire", "StrideEnv", "CadStrideAcquireCfg"),
                        ("Commands", "StrideCommandEnv", "CadStrideCommandsCfg"),
                        ("Speed", "StrideCommandEnv", "CadStrideSpeedCfg"),
                        ("Variation", "StrideCommandEnv", "CadStrideVariationCfg"),
                        ("Robust", "StrideCommandEnv", "CadStrideRobustCfg"),
                        ("Sustained", "StrideCommandEnv", "CadStrideSustainedCfg"),
                        ("Rough125", "StrideCommandEnv", "CadStrideRough125Cfg"),
                        ("RoughBumps25", "StrideCommandEnv", "CadStrideRoughBumps25Cfg"),
                        ("Stairs", "StrideCommandEnv", "CadStrideStairsCfg")):
    gym.register(
        id=f"Isaac-Locomotion-CurrentBodyV22-{stage}-Simple-Dog-Direct-v0",
        entry_point=f"simple_dog_task_current_body_v21.env:{env}",
        disable_env_checker=True,
        kwargs={"env_cfg_entry_point": f"{__name__}.env_cfg:{cfg}",
                "rl_games_cfg_entry_point": f"{__name__}.agents:rl_games_ppo_cfg.yaml"},
    )

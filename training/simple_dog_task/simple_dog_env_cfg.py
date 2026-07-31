"""Configuration for a conservative flat-ground velocity task.

The generated Onshape USD remains immutable. Isaac Lab overrides only runtime
solver and actuator settings while keeping the Publisher's mass, collision,
geometry, and drive data.
"""

from isaaclab_physx.physics import PhysxCfg

import isaaclab.sim as sim_utils
import isaaclab.terrains as terrain_gen
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg
from isaaclab.envs import DirectRLEnvCfg
from isaaclab.envs.common import ViewerCfg
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.sensors import ContactSensorCfg
from isaaclab.sim import SimulationCfg
from isaaclab.terrains import TerrainGeneratorCfg, TerrainImporterCfg
from isaaclab.utils.configclass import configclass

from simple_dog_tuning import load_tuning


DOG_USD = "/workspace/projects/training/assets/simple_dog_training.usda"
TUNING = load_tuning()

# Stable free-standing pose found by a 1,024-environment GPU calibration and
# then validated for 12 seconds with independent +/-0.035 rad perturbations.
# Isaac Lab consumes joint positions in radians.
CALIBRATED_JOINT_POS = {
    "_M1FJe8T6NDlY0LNLX": 0.0215978213,   # Front Right Hip
    "_M17lNIUcn80HD7Q0k": -1.2000883818,  # Back Right Hip
    "_MwP_D3xH5iroh0GMh": 0.9541545510,   # Front Left Hip
    "_MSisryrVCS27na0VO": 1.3999999762,   # Back Left Hip
    "_MX_hB5nqO3BDf8_Uf": -0.0237277560,  # Front Right Knee
    "_M9YA_lGt3xsD68dBn": -0.5214304924,  # Back Right Knee
    "_MbJy5CEqXTSl4WsxX": 0.1090470701,   # Front Left Knee
    "_MD8PhymI8YJME2GAl": 0.1626079530,   # Back Left Knee
}


SIMPLE_DOG_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=DOG_USD,
        activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            retain_accelerations=False,
            linear_damping=0.0,
            angular_damping=0.0,
            max_linear_velocity=10.0,
            max_angular_velocity=20.0,
            max_depenetration_velocity=0.5,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False,
            solver_position_iteration_count=8,
            solver_velocity_iteration_count=4,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.24),
        joint_pos=CALIBRATED_JOINT_POS,
        joint_vel={".*": 0.0},
    ),
    actuators={
        "legs": ImplicitActuatorCfg(
            joint_names_expr=[".*"],
            effort_limit_sim=1.37,
            velocity_limit_sim=8.0,
            stiffness=22.0,
            damping=0.8,
            armature=0.001,
        )
    },
    soft_joint_pos_limit_factor=0.95,
)


# The stock Isaac Lab rough terrain is sized for much larger quadrupeds.
# These ranges retain its flat/noise/slope/discrete-obstacle progression while
# scaling height and spacing to this dog's 0.16 m standing height.
SIMPLE_DOG_ROUGH_TERRAINS_CFG = TerrainGeneratorCfg(
    seed=42,
    curriculum=True,
    size=(4.0, 4.0),
    # The stock 10-20 m outer border is sized for much larger robots and made
    # this tiny dog's generated mesh exceed 1.3 million faces. RayCaster loads
    # that entire static mesh into Warp at initialization. Two metres still
    # fully encloses the 4 m tiles while avoiding the GB10 BVH startup wedge.
    border_width=2.0,
    num_rows=6,
    num_cols=8,
    horizontal_scale=0.05,
    vertical_scale=0.0025,
    slope_threshold=0.75,
    difficulty_range=(0.0, 1.0),
    use_cache=False,
    sub_terrains={
        "flat": terrain_gen.MeshPlaneTerrainCfg(proportion=0.20),
        "random_rough": terrain_gen.HfRandomUniformTerrainCfg(
            proportion=0.40,
            noise_range=(0.0025, 0.030),
            noise_step=0.0025,
            border_width=0.20,
        ),
        "pyramid_slope": terrain_gen.HfPyramidSlopedTerrainCfg(
            proportion=0.20,
            slope_range=(0.0, 0.18),
            platform_width=1.0,
            border_width=0.20,
        ),
        "inverted_pyramid_slope": terrain_gen.HfInvertedPyramidSlopedTerrainCfg(
            proportion=0.20,
            slope_range=(0.0, 0.18),
            platform_width=1.0,
            border_width=0.20,
        ),
    },
)

# The headless video recorder in Isaac Lab 3.0 copies ViewerCfg eye/lookat as
# absolute world coordinates and does not apply ViewerCfg.origin_type.  Use one
# deterministic validation tile centered at the world origin so the recorded
# robot remains in frame.  Training still uses the full 6x8 curriculum above.
SIMPLE_DOG_ROUGH_VALIDATION_TERRAIN_CFG = TerrainGeneratorCfg(
    seed=31415,
    curriculum=False,
    size=(4.0, 4.0),
    border_width=2.0,
    num_rows=1,
    num_cols=1,
    horizontal_scale=0.05,
    vertical_scale=0.0025,
    slope_threshold=0.75,
    difficulty_range=(0.65, 0.65),
    use_cache=False,
    sub_terrains={
        "random_rough": terrain_gen.HfRandomUniformTerrainCfg(
            proportion=1.0,
            noise_range=(0.0025, 0.030),
            noise_step=0.0025,
            border_width=0.20,
        ),
    },
)


@configclass
class SimpleDogFlatEnvCfg(DirectRLEnvCfg):
    episode_length_s = 12.0
    decimation = 4
    action_scale = 0.35
    action_space = 8
    observation_space = 38
    state_space = 0

    sim: SimulationCfg = SimulationCfg(
        dt=1 / 200,
        render_interval=decimation,
        physics=PhysxCfg(gpu_max_rigid_patch_count=2**18),
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
    )

    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="plane",
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
        debug_vis=False,
    )

    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs=512,
        env_spacing=1.5,
        replicate_physics=True,
    )

    robot: ArticulationCfg = SIMPLE_DOG_CFG.replace(prim_path="/World/envs/env_.*/Robot")
    contact_sensor: ContactSensorCfg = ContactSensorCfg(
        prim_path="/World/envs/env_.*/Robot/links/.*",
        history_length=3,
        update_period=0.005,
        track_air_time=True,
    )
    height_scanner = None
    height_observation_size = 0

    # The Onshape assembly's physical front is body -Y: front hips are at
    # y=-0.125 m and back hips are at y=+0.125 m. Commands use a semantic
    # (forward, lateral, yaw-rate) frame so positive forward never means the
    # exported body's +X side.
    command_forward = (
        TUNING["command_forward_min"],
        TUNING["command_forward_max"],
    )
    command_lateral = (0.0, 0.0)
    command_yaw = (0.0, 0.0)
    standing_command_fraction = 0.05

    # Minimal task structure adapted from Isaac Lab's Spot flat-ground task:
    # track the command, coordinate a diagonal gait, cycle all four feet, and
    # apply only the regularizers needed to reject unstable/sliding solutions.
    body_vel_reward_scale = TUNING["body_vel_reward_scale"]
    velocity_tracking_std = TUNING["velocity_tracking_std"]
    yaw_rate_reward_scale = TUNING["yaw_rate_reward_scale"]
    yaw_tracking_std = 0.50
    gait_reward_scale = TUNING["gait_reward_scale"]
    gait_std = 0.10
    gait_max_error = 0.20
    gait_velocity_threshold = 0.05
    feet_air_time_reward_scale = TUNING["feet_air_time_reward_scale"]
    feet_mode_time = 0.30
    fall_penalty_scale = -8.0
    air_time_variance_penalty_scale = TUNING["air_time_variance_penalty_scale"]
    base_motion_penalty_scale = TUNING["base_motion_penalty_scale"]
    base_orientation_penalty_scale = TUNING["base_orientation_penalty_scale"]
    action_smoothness_penalty_scale = TUNING[
        "action_smoothness_penalty_scale"
    ]
    foot_slip_penalty_scale = TUNING["foot_slip_penalty_scale"]
    undesired_contact_penalty_scale = TUNING[
        "undesired_contact_penalty_scale"
    ]

    termination_height = 0.105
    termination_projected_gravity_z = -0.55
    terrain_curriculum = False
    print_play_metrics = False


@configclass
class SimpleDogRoughEnvCfg(SimpleDogFlatEnvCfg):
    """Curriculum terrain continuation of the validated flat-ground task."""

    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="generator",
        terrain_generator=SIMPLE_DOG_ROUGH_TERRAINS_CFG,
        # Continuous PPO blocks span several complete episodes, allowing the
        # terrain curriculum to move robots down or up based on performance.
        # Keep rows 0-3 in the initial distribution: on this Isaac/GB10 build,
        # restricting the generated importer to rows 0-1 repeatedly wedges
        # SimulationContext.reset() before PPO begins.
        max_init_terrain_level=3,
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
        debug_vis=False,
    )
    terrain_curriculum = True
    # A zero command makes remaining upright a locally attractive solution on
    # difficult terrain.  Rough locomotion is trained only on actual traversal
    # commands; standing remains covered by the preserved flat controller.
    standing_command_fraction = 0.0
    observation_space = 73
    # Proprioceptive rough locomotion follows Isaac Lab's Spot cobblestone
    # example and avoids the legacy single-mesh RayCaster, whose Warp BVH
    # initialization is nondeterministic on this 1.38M-face GB10 terrain.
    # Keep 35 neutral compatibility inputs so the existing 73-input policy and
    # observation normalizer can be continued without architectural surgery.
    height_scanner = None
    height_observation_size = 35


@configclass
class SimpleDogRoughNoScanDiagnosticEnvCfg(SimpleDogRoughEnvCfg):
    """Diagnostic rough task that isolates the terrain mesh from ray casting."""

    observation_space = 38
    height_scanner = None
    height_observation_size = 0


@configclass
class SimpleDogFlatPlayEnvCfg(SimpleDogFlatEnvCfg):
    """Single-robot forward-walking demonstration configuration."""

    episode_length_s = 60.0

    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs=1,
        env_spacing=1.5,
        replicate_physics=True,
    )
    viewer: ViewerCfg = ViewerCfg(
        eye=(0.85, 0.85, 0.45),
        lookat=(0.10, 0.0, 0.14),
        origin_type="asset_root",
        env_index=0,
        asset_name="robot",
    )

    # A fixed command makes the visual test unambiguous: the dog must move
    # forward rather than merely stand or happen to sample a near-zero command.
    command_forward = (0.25, 0.25)
    command_lateral = (0.0, 0.0)
    command_yaw = (0.0, 0.0)
    standing_command_fraction = 0.0
    print_play_metrics = True


@configclass
class SimpleDogRoughPlayEnvCfg(SimpleDogRoughEnvCfg):
    """Multi-robot overview across the generated rough-terrain curriculum."""

    episode_length_s = 60.0
    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="generator",
        terrain_generator=SIMPLE_DOG_ROUGH_TERRAINS_CFG,
        max_init_terrain_level=3,
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
        debug_vis=False,
    )
    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs=4,
        env_spacing=1.5,
        replicate_physics=True,
    )
    viewer: ViewerCfg = ViewerCfg(
        eye=(12.0, 12.0, 8.0),
        lookat=(0.0, 0.0, 0.0),
        origin_type="world",
    )
    command_forward = (0.25, 0.25)
    command_lateral = (0.0, 0.0)
    command_yaw = (0.0, 0.0)
    standing_command_fraction = 0.0
    terrain_curriculum = True
    print_play_metrics = True


@configclass
class SimpleDogRoughValidationEnvCfg(SimpleDogRoughEnvCfg):
    """Close-up, single-robot rough rollout used for policy acceptance.

    The streamed rough-terrain showcase deliberately uses a distant world
    camera so several terrain types fit in one view.  Automated validation
    needs the opposite: one robot large enough to inspect its feet and body
    motion.  Keeping these as separate tasks prevents showcase changes from
    silently invalidating visual evidence.
    """

    episode_length_s = 60.0
    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="generator",
        terrain_generator=SIMPLE_DOG_ROUGH_VALIDATION_TERRAIN_CFG,
        max_init_terrain_level=0,
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
        debug_vis=False,
    )
    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs=1,
        env_spacing=1.5,
        replicate_physics=True,
    )
    viewer: ViewerCfg = ViewerCfg(
        eye=(0.85, 0.85, 0.45),
        lookat=(0.10, 0.0, 0.14),
        origin_type="world",
    )
    command_forward = (0.25, 0.25)
    command_lateral = (0.0, 0.0)
    command_yaw = (0.0, 0.0)
    standing_command_fraction = 0.0
    terrain_curriculum = True
    print_play_metrics = True

"""Validated, UI-friendly control profiles for the Isaac Lab dog tasks.

The JSON profile is the contract between the browser, the Windows launchers,
and the Python configuration imported inside Isaac Lab. Keep this module free
of Isaac dependencies so it can also validate profiles on Windows and in CI.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
STAGES = ("V2Core", "V2Robust", "V2Goal", "V2Rough")
SEMANTIC_JOINTS_12 = tuple(
    f"{leg}_{joint}"
    for leg in ("front_right", "front_left", "back_right", "back_left")
    for joint in ("hip_abduction", "hip_flexion", "knee_flexion")
)


FIELD_GROUPS: list[dict[str, Any]] = [
    {
        "id": "run",
        "title": "Training run",
        "summary": "Choose the curriculum stage and how much GPU work to run.",
        "fields": [
            {"path": "training.stage", "label": "Stage", "type": "select", "options": list(STAGES), "description": "V2 stages are intentionally sequential. Robust and Goal require a passing V2 checkpoint; Rough should normally follow Goal."},
            {"path": "training.num_envs", "label": "Parallel environments", "type": "integer", "min": 128, "max": 16384, "step": 128, "description": "Number of robot copies simulated at once. More copies improve sample throughput but consume more GPU memory."},
            {"path": "training.max_iterations", "label": "Training cycles", "type": "integer", "min": 1, "max": 100000, "step": 25, "description": "Total PPO epochs for this run. For a continuation this must be higher than the epoch stored in the source checkpoint."},
            {"path": "training.checkpoint", "label": "Source checkpoint", "type": "text", "description": "Optional V2 .pth checkpoint. Robust and Goal require one. V1 checkpoints are rejected because their observations differ."},
            {"path": "training.seed", "label": "Random seed", "type": "integer", "min": 0, "max": 2147483647, "step": 1, "expert": True, "description": "Controls repeatable pseudo-random choices. Deterministic evaluation still uses its own fixed seed suite."},
            {"path": "training.record_video", "label": "Training video", "type": "boolean", "description": "Records periodic rollout clips inside Isaac Lab so behavior can be inspected alongside scalar reward. Rendering reduces training throughput."},
            {"path": "training.video_interval", "label": "Video interval", "type": "integer", "min": 100, "max": 10000000, "step": 100, "unit": "steps", "description": "Number of environment steps between recorded clips. This is a capture interval, not a PPO epoch count."},
            {"path": "training.video_length", "label": "Video length", "type": "integer", "min": 50, "max": 5000, "step": 50, "unit": "steps", "description": "Frames in each rollout clip. Longer clips provide better behavioral evidence but pause high-throughput headless training for longer."},
        ],
    },
    {
        "id": "surface",
        "title": "Surface",
        "summary": "Select the terrain family and the contact material Isaac Sim will build.",
        "fields": [
            {"path": "environment.surface", "label": "Surface", "type": "select", "options": ["Flat", "Random rough", "Slopes", "Mixed curriculum"], "description": "Terrain beneath the robot. Selecting a non-flat surface also selects the V2 Rough task; selecting Flat returns to V2 Core. Save before starting because a running Isaac scene cannot change terrain."},
            {"path": "terrain.roughness_min", "label": "Roughness minimum", "type": "number", "min": 0, "max": 0.25, "step": 0.0025, "unit": "m", "description": "Smallest vertical displacement in random rough terrain."},
            {"path": "terrain.roughness_max", "label": "Roughness maximum", "type": "number", "min": 0, "max": 0.5, "step": 0.0025, "unit": "m", "description": "Largest vertical displacement at maximum curriculum difficulty."},
            {"path": "terrain.slope_max", "label": "Maximum slope", "type": "number", "min": 0, "max": 0.8, "step": 0.01, "description": "Steepest generated pyramid slope at maximum difficulty."},
            {"path": "terrain.tile_size", "label": "Terrain tile size", "type": "number", "min": 1, "max": 20, "step": 0.5, "unit": "m", "expert": True, "description": "Width and depth of one generated curriculum tile."},
            {"path": "environment.static_friction", "label": "Static friction", "type": "number", "min": 0, "max": 3, "step": 0.05, "expert": True, "description": "Resistance before a stationary contact starts sliding. Match the expected foot and floor materials."},
            {"path": "environment.dynamic_friction", "label": "Dynamic friction", "type": "number", "min": 0, "max": 3, "step": 0.05, "expert": True, "description": "Resistance after contact is already sliding. It should normally be no greater than static friction."},
            {"path": "environment.restitution", "label": "Restitution", "type": "number", "min": 0, "max": 1, "step": 0.05, "expert": True, "description": "Contact bounciness. Legged locomotion surfaces usually use a value near zero."}
        ]
    },
    {
        "id": "physics",
        "title": "Simulator physics",
        "summary": "Control integration, articulation solving, contacts, and environment layout.",
        "expert": True,
        "fields": [
            {"path": "environment.physics_hz", "label": "Physics rate", "type": "number", "min": 50, "max": 1000, "step": 10, "unit": "Hz", "expert": True, "description": "PhysX integration frequency. Higher rates improve fast-contact resolution but make every simulated second more expensive."},
            {"path": "physics.solver_position_iterations", "label": "Position solver iterations", "type": "integer", "min": 1, "max": 64, "step": 1, "expert": True, "description": "Iterations used to resolve articulation positions and contacts each physics step."},
            {"path": "physics.solver_velocity_iterations", "label": "Velocity solver iterations", "type": "integer", "min": 0, "max": 64, "step": 1, "expert": True, "description": "Iterations used to resolve joint and contact velocities each step."},
            {"path": "physics.self_collisions", "label": "Self collisions", "type": "boolean", "expert": True, "description": "Allows robot links to collide with each other. Enable only if the authored collision geometry is clean enough."},
            {"path": "physics.max_depenetration_velocity", "label": "Max depenetration speed", "type": "number", "min": 0.01, "max": 20, "step": 0.05, "unit": "m/s", "expert": True, "description": "Caps how quickly PhysX separates overlapping bodies after a bad spawn or collision."},
            {"path": "physics.contact_patch_capacity", "label": "GPU contact capacity", "type": "integer", "min": 65536, "max": 4194304, "step": 65536, "expert": True, "description": "Maximum simultaneous rigid contact patches. Rough terrain with many environments needs more headroom."},
            {"path": "environment.env_spacing", "label": "Environment spacing", "type": "number", "min": 0.25, "max": 20, "step": 0.25, "unit": "m", "expert": True, "description": "Distance between cloned robots. It must exceed the robot and nearby terrain footprint."}
        ]
    },
    {
        "id": "motion",
        "title": "Motion & timing",
        "summary": "Set the policy rate, target motion, and action size.",
        "fields": [
            {"path": "environment.control_hz", "label": "Policy rate", "type": "number", "min": 10, "max": 200, "step": 5, "unit": "Hz", "description": "How often the policy sends joint targets. It must divide the physics rate exactly and must match the eventual hardware controller."},
            {"path": "environment.action_scale", "label": "Action range", "type": "number", "min": 0.01, "max": 1.5, "step": 0.01, "unit": "rad", "description": "Maximum joint-position residual around the calibrated standing pose. Too large makes violent actions easy; too small can prevent useful steps."},
            {"path": "environment.action_delta_limit", "label": "Action slew limit", "type": "number", "min": 0.01, "max": 2.0, "step": 0.01, "unit": "normalized/frame", "description": "Largest policy-action change allowed per control frame. This must match the real controller's per-frame servo safety limit."},
            {"path": "environment.stationary_planar_deadband", "label": "Stationary planar deadband", "type": "number", "min": 0, "max": 0.25, "step": 0.005, "unit": "m/s", "description": "Commands inside this planar-speed deadband slew to the validated four-foot stance action."},
            {"path": "environment.stationary_yaw_deadband", "label": "Stationary yaw deadband", "type": "number", "min": 0, "max": 0.30, "step": 0.005, "unit": "rad/s", "description": "Commands inside both stationary deadbands use the same four-foot stance contract in simulation and deployment."},
            {"path": "commands.forward_min", "label": "Minimum forward speed", "type": "number", "min": -3, "max": 2, "step": 0.01, "unit": "m/s", "description": "Smallest commanded body-forward velocity. Negative values train reverse motion."},
            {"path": "commands.forward_max", "label": "Maximum forward speed", "type": "number", "min": 0.01, "max": 3, "step": 0.01, "unit": "m/s", "description": "Largest commanded forward velocity. Increase only after stable slower locomotion passes evaluation."},
            {"path": "commands.lateral_min", "label": "Minimum lateral speed", "type": "number", "min": -3, "max": 3, "step": 0.01, "unit": "m/s", "description": "Smallest sideways command in the robot's semantic body frame."},
            {"path": "commands.lateral_max", "label": "Maximum lateral speed", "type": "number", "min": -3, "max": 3, "step": 0.01, "unit": "m/s", "description": "Largest sideways command. Set both lateral limits to zero for forward-only training."},
            {"path": "commands.yaw_max", "label": "Maximum yaw rate", "type": "number", "min": 0, "max": 3, "step": 0.05, "unit": "rad/s", "description": "Magnitude of left/right turning commands."},
            {"path": "commands.straight_fraction", "label": "Straight-command share", "type": "number", "min": 0, "max": 1, "step": 0.05, "description": "Share of locomotion samples that request zero yaw rate."},
            {"path": "commands.low_speed_straight_fraction", "label": "Slow-straight share", "type": "number", "min": 0, "max": 1, "step": 0.05, "description": "Share of locomotion samples forced to the minimum forward speed with zero yaw, teaching precise slow approach behavior."},
            {"path": "commands.high_speed_straight_fraction", "label": "Fast-straight share", "type": "number", "min": 0, "max": 1, "step": 0.05, "description": "Share of locomotion samples forced to the maximum forward speed with zero yaw, allowing high-speed foot slip to be trained directly."},
            {"path": "commands.hold_min_s", "label": "Command hold minimum", "type": "number", "min": 0.1, "max": 60, "step": 0.1, "unit": "s", "expert": True, "description": "Shortest time before sampling a new motion target."},
            {"path": "commands.hold_max_s", "label": "Command hold maximum", "type": "number", "min": 0.1, "max": 60, "step": 0.1, "unit": "s", "expert": True, "description": "Longest time before sampling a new motion target."},
            {"path": "commands.smoothing_s", "label": "Command smoothing", "type": "number", "min": 0.01, "max": 5, "step": 0.01, "unit": "s", "expert": True, "description": "Time constant used to ramp toward a new command instead of changing it instantly."},
            {"path": "commands.standing_fraction", "label": "Standing command share", "type": "number", "min": 0, "max": 1, "step": 0.05, "description": "Fraction of episodes that request zero motion. Normally zero in Core/Rough and introduced for Goal completion."},
            {"path": "commands.turn_fraction", "label": "Turn-in-place share", "type": "number", "min": 0, "max": 1, "step": 0.05, "description": "Fraction of commands that request turning without forward motion."},
            {"path": "commands.curve_right_fraction", "label": "Right-curve share", "type": "number", "min": 0, "max": 1, "step": 0.05, "expert": True, "description": "Share of forward curved-walking samples that rotate right. Adjust only to correct measured directional gait asymmetry."},
            {"path": "commands.turn_right_fraction", "label": "Right-turn share", "type": "number", "min": 0, "max": 1, "step": 0.05, "expert": True, "description": "Share of turn-in-place samples that rotate right. Use 0.5 for a symmetric robot and adjust only to correct measured directional asymmetry."},
            {"path": "commands.turn_yaw_min", "label": "Turn rate minimum", "type": "number", "min": 0, "max": 3, "step": 0.05, "unit": "rad/s", "expert": True, "description": "Smallest magnitude used for turn-in-place commands."},
            {"path": "commands.turn_yaw_max", "label": "Turn rate maximum", "type": "number", "min": 0, "max": 3, "step": 0.05, "unit": "rad/s", "expert": True, "description": "Largest magnitude used for turn-in-place commands."},
        ],
    },
    {
        "id": "initialization",
        "title": "Start & reset",
        "summary": "Control the robot's authored start pose and randomized episode resets.",
        "fields": [
            {"path": "robot.start_position.0", "label": "Start X", "type": "number", "min": -10, "max": 10, "step": 0.01, "unit": "m", "description": "Initial root X position relative to each environment origin."},
            {"path": "robot.start_position.1", "label": "Start Y", "type": "number", "min": -10, "max": 10, "step": 0.01, "unit": "m", "description": "Initial root Y position relative to each environment origin."},
            {"path": "robot.start_position.2", "label": "Start height", "type": "number", "min": 0.02, "max": 5, "step": 0.005, "unit": "m", "description": "Root height at reset. It must place the feet near the ground without interpenetrating it."},
            {"path": "robot.start_rotation_deg.0", "label": "Start roll", "type": "number", "min": -180, "max": 180, "step": 1, "unit": "deg", "expert": True, "description": "Authored root roll before reset randomization."},
            {"path": "robot.start_rotation_deg.1", "label": "Start pitch", "type": "number", "min": -180, "max": 180, "step": 1, "unit": "deg", "expert": True, "description": "Authored root pitch before reset randomization."},
            {"path": "robot.start_rotation_deg.2", "label": "Start yaw", "type": "number", "min": -180, "max": 180, "step": 1, "unit": "deg", "expert": True, "description": "Authored root yaw before reset randomization."},
            {"path": "reset.small_tilt_deg", "label": "Normal tilt", "type": "number", "min": 0, "max": 45, "step": 1, "unit": "deg", "description": "Maximum roll/pitch magnitude for ordinary resets."},
            {"path": "reset.large_tilt_deg", "label": "Challenge tilt", "type": "number", "min": 0, "max": 60, "step": 1, "unit": "deg", "description": "Maximum tilt for the harder reset subset. Fallen-pose self-righting remains a separate skill."},
            {"path": "reset.large_tilt_fraction", "label": "Challenge reset share", "type": "number", "min": 0, "max": 1, "step": 0.05, "unit": "fraction", "description": "Fraction of resets sampled from the challenge-tilt range."},
            {"path": "reset.randomize_yaw", "label": "Randomize start heading", "type": "boolean", "description": "Randomizes absolute yaw while keeping commands in the robot's local semantic frame."},
            {"path": "reset.joint_position_noise", "label": "Joint reset position noise", "type": "number", "min": 0, "max": 1.5, "step": 0.01, "unit": "rad", "expert": True, "description": "Uniform encoder-space perturbation around each joint's standing position at reset."},
            {"path": "reset.joint_velocity_noise", "label": "Joint reset velocity noise", "type": "number", "min": 0, "max": 20, "step": 0.1, "unit": "rad/s", "expert": True, "description": "Uniform initial joint-speed perturbation. Keep this small until the standing pose is stable."},
            {"path": "environment.episode_length_s", "label": "Episode duration", "type": "number", "min": 1, "max": 120, "step": 1, "unit": "s", "expert": True, "description": "Maximum simulated time before an episode resets if it has not already terminated."},
            {"path": "environment.termination_height", "label": "Fall height", "type": "number", "min": 0.01, "max": 2, "step": 0.005, "unit": "m", "expert": True, "description": "Root height below which the robot counts as fallen. Scale this with the new robot's standing height."},
        ],
    },
    {
        "id": "randomization",
        "title": "Domain randomization",
        "summary": "Vary masses, actuator response, center of mass, and contact material across parallel Isaac Lab environments.",
        "fields": [
            {"path": "domain_randomization.enabled", "label": "Domain randomization", "type": "boolean", "description": "Samples physical variants once per parallel environment. This is the mass/friction randomization shown in NVIDIA's quadruped workflow."},
            {"path": "domain_randomization.base_mass_scale_min", "label": "Base mass scale minimum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Smallest multiplicative chassis-mass factor. Scale is safer than adding kilograms when swapping robot sizes."},
            {"path": "domain_randomization.base_mass_scale_max", "label": "Base mass scale maximum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Largest multiplicative chassis-mass factor. Inertia is scaled consistently with mass."},
            {"path": "domain_randomization.link_mass_scale_min", "label": "Link mass scale minimum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Smallest independent mass factor for each non-chassis link; inertia is scaled with it."},
            {"path": "domain_randomization.link_mass_scale_max", "label": "Link mass scale maximum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Largest independent mass factor for each non-chassis link."},
            {"path": "domain_randomization.actuator_drive_scale_min", "label": "Actuator drive scale minimum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Smallest per-joint stiffness and damping factor, representing drive-response variation."},
            {"path": "domain_randomization.actuator_drive_scale_max", "label": "Actuator drive scale maximum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Largest per-joint stiffness and damping factor."},
            {"path": "domain_randomization.actuator_effort_scale_min", "label": "Actuator torque scale minimum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Smallest independent factor applied to each joint's torque limit."},
            {"path": "domain_randomization.actuator_effort_scale_max", "label": "Actuator torque scale maximum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Largest independent factor applied to each joint's torque limit."},
            {"path": "domain_randomization.actuator_velocity_scale_min", "label": "Actuator speed scale minimum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Smallest independent factor applied to each joint's speed limit."},
            {"path": "domain_randomization.actuator_velocity_scale_max", "label": "Actuator speed scale maximum", "type": "number", "min": 0.1, "max": 3, "step": 0.01, "description": "Largest independent factor applied to each joint's speed limit."},
            {"path": "domain_randomization.base_com_x", "label": "Base COM X range", "type": "number", "min": 0, "max": 0.5, "step": 0.005, "unit": "m", "expert": True, "description": "Symmetric center-of-mass offset along body X, sampled between minus and plus this value."},
            {"path": "domain_randomization.base_com_y", "label": "Base COM Y range", "type": "number", "min": 0, "max": 0.5, "step": 0.005, "unit": "m", "expert": True, "description": "Symmetric center-of-mass offset along body Y."},
            {"path": "domain_randomization.base_com_z", "label": "Base COM Z range", "type": "number", "min": 0, "max": 0.5, "step": 0.005, "unit": "m", "expert": True, "description": "Symmetric center-of-mass offset along body Z."},
            {"path": "domain_randomization.robot_static_friction_min", "label": "Robot static friction minimum", "type": "number", "min": 0, "max": 3, "step": 0.05, "description": "Lowest static friction sampled for the robot collision shapes."},
            {"path": "domain_randomization.robot_static_friction_max", "label": "Robot static friction maximum", "type": "number", "min": 0, "max": 3, "step": 0.05, "description": "Highest static friction sampled for the robot collision shapes."},
            {"path": "domain_randomization.robot_dynamic_friction_min", "label": "Robot dynamic friction minimum", "type": "number", "min": 0, "max": 3, "step": 0.05, "description": "Lowest sliding-friction value sampled for robot collision shapes."},
            {"path": "domain_randomization.robot_dynamic_friction_max", "label": "Robot dynamic friction maximum", "type": "number", "min": 0, "max": 3, "step": 0.05, "description": "Highest sliding-friction value; sampled values are clamped below their static friction."},
            {"path": "domain_randomization.robot_restitution_min", "label": "Robot restitution minimum", "type": "number", "min": 0, "max": 1, "step": 0.05, "expert": True, "description": "Lowest collision bounciness sampled for robot shapes."},
            {"path": "domain_randomization.robot_restitution_max", "label": "Robot restitution maximum", "type": "number", "min": 0, "max": 1, "step": 0.05, "expert": True, "description": "Highest collision bounciness sampled for robot shapes."},
            {"path": "domain_randomization.material_buckets", "label": "Material buckets", "type": "integer", "min": 1, "max": 1024, "step": 1, "expert": True, "description": "Number of reusable PhysX material variants. Bucketed sampling avoids the PhysX unique-material limit."},
        ],
    },
    {
        "id": "actuators",
        "title": "Actuators",
        "summary": "Tune joint drives globally, then override individual joints in the robot table.",
        "fields": [
            {"path": "actuators.stiffness", "label": "Joint stiffness", "type": "number", "min": 0, "max": 1000, "step": 0.5, "unit": "N m/rad", "description": "Proportional position-drive gain. Higher values track targets more tightly but can oscillate or become unrealistically rigid."},
            {"path": "actuators.damping", "label": "Joint damping", "type": "number", "min": 0, "max": 100, "step": 0.1, "unit": "N m s/rad", "description": "Velocity feedback that resists oscillation. Too much makes motion sluggish and can hide insufficient motor authority."},
            {"path": "actuators.effort_limit", "label": "Torque limit", "type": "number", "min": 0.01, "max": 1000, "step": 0.01, "unit": "N m", "description": "Maximum simulated actuator torque. Match measured hardware capability; an optimistic value produces policies the real robot cannot execute."},
            {"path": "actuators.velocity_limit", "label": "Velocity limit", "type": "number", "min": 0.01, "max": 100, "step": 0.1, "unit": "rad/s", "description": "Maximum joint speed used by the simulator actuator model."},
            {"path": "actuators.armature", "label": "Armature", "type": "number", "min": 0, "max": 10, "step": 0.0001, "unit": "kg m²", "expert": True, "description": "Additional rotor inertia reflected at the joint. It can improve numerical realism but should be based on the drivetrain."},
            {"path": "actuators.soft_limit_factor", "label": "Soft joint-limit factor", "type": "number", "min": 0.1, "max": 1, "step": 0.01, "expert": True, "description": "Shrinks the authored joint range for policy targets, leaving margin before hard limits."},
        ],
    },
    {
        "id": "disturbance",
        "title": "Robustness",
        "summary": "Randomize pushes and deployable sensor measurements.",
        "fields": [
            {"path": "disturbance.push_probability", "label": "Push probability", "type": "number", "min": 0, "max": 1, "step": 0.05, "description": "Chance of applying a disturbance when a push timer expires."},
            {"path": "disturbance.push_linear_velocity", "label": "Linear push", "type": "number", "min": 0, "max": 3, "step": 0.05, "unit": "m/s", "description": "Magnitude added to planar base velocity for a simulated shove."},
            {"path": "disturbance.push_yaw_velocity", "label": "Yaw push", "type": "number", "min": 0, "max": 3, "step": 0.05, "unit": "rad/s", "description": "Magnitude added to base yaw velocity during a shove."},
            {"path": "disturbance.push_interval_min_s", "label": "Push interval minimum", "type": "number", "min": 0.1, "max": 120, "step": 0.5, "unit": "s", "expert": True, "description": "Shortest time between opportunities to apply a push."},
            {"path": "disturbance.push_interval_max_s", "label": "Push interval maximum", "type": "number", "min": 0.1, "max": 120, "step": 0.5, "unit": "s", "expert": True, "description": "Longest time between opportunities to apply a push."},
            {"path": "noise.enabled", "label": "Sensor noise", "type": "boolean", "description": "Adds bounded noise only to signals intended to exist on the physical robot."},
            {"path": "noise.gyro", "label": "Gyro noise", "type": "number", "min": 0, "max": 2, "step": 0.01, "unit": "rad/s", "expert": True, "description": "Uniform angular-velocity noise applied to the simulated body gyro."},
            {"path": "noise.gravity", "label": "Gravity estimate noise", "type": "number", "min": 0, "max": 0.5, "step": 0.005, "expert": True, "description": "Noise on projected gravity, representing attitude-estimator error rather than raw accelerometer noise."},
            {"path": "noise.joint_position", "label": "Encoder position noise", "type": "number", "min": 0, "max": 0.5, "step": 0.005, "unit": "rad", "expert": True, "description": "Uniform position error added to each joint encoder observation."},
            {"path": "noise.joint_velocity", "label": "Joint velocity noise", "type": "number", "min": 0, "max": 10, "step": 0.05, "unit": "rad/s", "expert": True, "description": "Noise on filtered or derived joint velocity measurements."},
        ],
    },
    {
        "id": "rewards",
        "title": "Rewards & safety",
        "summary": "Shape behavior without weakening deterministic promotion gates.",
        "fields": [
            {"path": "rewards.locomotion", "label": "Locomotion reward", "type": "number", "min": 0, "max": 25, "step": 0.1, "description": "Weight for signed progress and velocity tracking. Standing earns no moving-command credit."},
            {"path": "rewards.yaw", "label": "Yaw tracking reward", "type": "number", "min": 0, "max": 20, "step": 0.1, "description": "Weight for tracking commanded turn rate and signed turn progress."},
            {"path": "rewards.diagonal_gait", "label": "Diagonal gait prior", "type": "number", "min": 0, "max": 10, "step": 0.1, "description": "Small progress-gated prior encouraging diagonal trot timing. Keep this unless evaluation proves another gait is needed."},
            {"path": "rewards.complete_gait_cycle", "label": "Four-foot cycle reward", "type": "number", "min": 0, "max": 200, "step": 1, "description": "Rewards a landing only when it advances the least-used foot, so repeated three-legged cycles earn nothing."},
            {"path": "rewards.reference_trot", "label": "Reference trot reward", "type": "number", "min": 0, "max": 20, "step": 0.1, "description": "During commanded motion, rewards exact alternating diagonal contact and penalizes planted or three-legged contact patterns."},
            {"path": "rewards.reference_trot_period_s", "label": "Reference trot period", "type": "number", "min": 0.1, "max": 2, "step": 0.01, "unit": "s", "expert": True, "description": "Deployable clock period for the alternating diagonal-pair contact reference."},
            {"path": "rewards.foot_clearance", "label": "Swing-foot clearance reward", "type": "number", "min": 0, "max": 20, "step": 0.1, "description": "Rewards airborne feet for reaching a robot-scaled clearance above their neutral COM height instead of dragging or shuffling."},
            {"path": "rewards.target_foot_clearance_m", "label": "Target foot clearance", "type": "number", "min": 0.001, "max": 0.2, "step": 0.001, "unit": "m", "expert": True, "description": "Desired vertical rise of an airborne foot-link COM relative to its neutral height. Scale this to the physical leg."},
            {"path": "rewards.nominal_foot_com_z_from_base_m", "label": "Neutral foot COM height", "type": "number", "min": -2, "max": 0, "step": 0.001, "unit": "m", "expert": True, "description": "Measured neutral foot-link COM Z relative to the base actor. This is asset-specific and is verified by the kinematics audit."},
            {"path": "rewards.foot_clearance_std", "label": "Clearance tolerance", "type": "number", "min": 0.001, "max": 0.2, "step": 0.001, "unit": "m", "expert": True, "description": "Width of the swing-clearance tracking reward."},
            {"path": "rewards.air_time_variance_penalty", "label": "Gait-phase variance penalty", "type": "number", "min": -20, "max": 0, "step": 0.1, "description": "Penalizes unequal phase durations across the four feet so one leg cannot lag behind the other three."},
            {"path": "rewards.diagonal_joint_symmetry", "label": "Diagonal joint symmetry", "type": "number", "min": 0, "max": 20, "step": 0.1, "description": "Rewards matching semantic joint residuals within FR/BL and FL/BR diagonal pairs. Requires a verified mirrored joint-sign map."},
            {"path": "rewards.diagonal_joint_symmetry_std", "label": "Joint symmetry tolerance", "type": "number", "min": 0.001, "max": 2, "step": 0.01, "unit": "rad²", "expert": True, "description": "Tolerance for mean squared diagonal-pair joint mismatch."},
            {"path": "rewards.prolonged_foot_air_penalty", "label": "Stuck gait-phase penalty", "type": "number", "min": -20, "max": 0, "step": 0.1, "description": "Penalty for keeping any foot continuously airborne or planted beyond a plausible gait window."},
            {"path": "rewards.stability_penalty", "label": "Instability penalty", "type": "number", "min": -20, "max": 0, "step": 0.1, "description": "Penalizes tilt, vertical motion, roll, and pitch rates."},
            {"path": "rewards.action_rate_penalty", "label": "Action-rate penalty", "type": "number", "min": -10, "max": 0, "step": 0.01, "description": "Discourages abrupt target changes between policy steps."},
            {"path": "rewards.foot_slip_penalty", "label": "Foot-slip penalty", "type": "number", "min": -20, "max": 0, "step": 0.05, "description": "Penalizes horizontal velocity while a foot is in contact."},
            {"path": "rewards.undesired_contact_penalty", "label": "Body-contact penalty", "type": "number", "min": -20, "max": 0, "step": 0.1, "description": "Penalizes contact from links that should not touch the terrain."},
            {"path": "rewards.fall_penalty", "label": "Fall penalty", "type": "number", "min": -100, "max": 0, "step": 0.5, "description": "One-time terminal penalty. It must remain large enough that ending an unsuccessful episode early is never profitable."},
            {"path": "rewards.velocity_tracking_std", "label": "Velocity tolerance", "type": "number", "min": 0.01, "max": 3, "step": 0.01, "expert": True, "description": "Width of the velocity tracking score. Smaller values demand more exact tracking."},
            {"path": "rewards.stationary_velocity_std", "label": "Stop velocity tolerance", "type": "number", "min": 0.01, "max": 1, "step": 0.01, "unit": "m/s", "expert": True, "description": "Narrow centered tolerance used when forward and lateral commands are zero."},
            {"path": "rewards.uncommanded_motion_penalty", "label": "Uncommanded translation penalty", "type": "number", "min": -100, "max": 0, "step": 0.5, "description": "Penalizes planar speed during stand and rotate-in-place commands."},
            {"path": "rewards.yaw_tracking_std", "label": "Yaw tolerance", "type": "number", "min": 0.01, "max": 3, "step": 0.01, "expert": True, "description": "Width of the yaw-rate tracking score."},
            {"path": "rewards.diagonal_gait_std", "label": "Gait timing tolerance", "type": "number", "min": 0.001, "max": 2, "step": 0.01, "unit": "s²", "expert": True, "description": "Tolerance in the diagonal-pair timing similarity."},
            {"path": "rewards.max_foot_air_time_s", "label": "Maximum swing time", "type": "number", "min": 0.05, "max": 3, "step": 0.05, "unit": "s", "expert": True, "description": "Air time beyond this threshold removes positive locomotion credit and triggers the prolonged-air penalty."},
            {"path": "rewards.max_foot_contact_time_s", "label": "Maximum continuous stance", "type": "number", "min": 0.05, "max": 3, "step": 0.05, "unit": "s", "expert": True, "description": "Continuous contact beyond this threshold removes positive motion credit and penalizes a planted-foot shortcut."},
            {"path": "rewards.max_gait_cycle_interval_s", "label": "Maximum four-foot cycle interval", "type": "number", "min": 0.1, "max": 3, "step": 0.05, "unit": "s", "expert": True, "description": "Positive motion credit decays unless the least-used foot completes another landing within this interval."},
        ],
    },
    {
        "id": "ppo",
        "title": "PPO (advanced)",
        "summary": "Optimizer and rollout settings used by RL-Games.",
        "expert": True,
        "fields": [
            {"path": "ppo.learning_rate", "label": "Learning rate", "type": "number", "min": 0.000001, "max": 0.01, "step": 0.000001, "expert": True, "description": "Optimizer step size. Large changes can rapidly destroy a good continuation checkpoint."},
            {"path": "ppo.learning_rate_schedule", "label": "Learning-rate schedule", "type": "select", "options": ["adaptive", "fixed"], "expert": True, "description": "Adaptive changes the rate from KL divergence. Fixed keeps the requested rate exact and is safer for delicate checkpoint refinement."},
            {"path": "ppo.horizon_length", "label": "Rollout horizon", "type": "integer", "min": 8, "max": 4096, "step": 8, "expert": True, "description": "Steps collected per environment before each PPO update."},
            {"path": "ppo.minibatch_size", "label": "Minibatch size", "type": "integer", "min": 128, "max": 1048576, "step": 128, "expert": True, "description": "Samples per gradient minibatch. The full batch (environments × horizon) must divide evenly by this value."},
            {"path": "ppo.mini_epochs", "label": "Optimization passes", "type": "integer", "min": 1, "max": 30, "step": 1, "expert": True, "description": "How many times PPO revisits each rollout batch."},
            {"path": "ppo.gamma", "label": "Discount factor", "type": "number", "min": 0.8, "max": 1, "step": 0.001, "expert": True, "description": "How strongly future reward affects the current update."},
            {"path": "ppo.gae_lambda", "label": "GAE lambda", "type": "number", "min": 0.8, "max": 1, "step": 0.001, "expert": True, "description": "Bias/variance tradeoff for generalized advantage estimation (called tau by RL-Games)."},
            {"path": "ppo.entropy_coefficient", "label": "Entropy coefficient", "type": "number", "min": 0, "max": 0.2, "step": 0.001, "expert": True, "description": "Rewards action-distribution exploration. Too high can prevent stable convergence."},
            {"path": "ppo.clip_range", "label": "PPO clip range", "type": "number", "min": 0.01, "max": 1, "step": 0.01, "expert": True, "description": "Limits how far a policy update may move from the behavior that generated the rollout."},
            {"path": "ppo.kl_threshold", "label": "KL threshold", "type": "number", "min": 0.0001, "max": 1, "step": 0.001, "expert": True, "description": "Target divergence used by RL-Games' adaptive learning-rate schedule."},
            {"path": "ppo.grad_norm", "label": "Gradient norm limit", "type": "number", "min": 0.01, "max": 100, "step": 0.1, "expert": True, "description": "Maximum gradient norm before clipping."},
            {"path": "ppo.critic_coefficient", "label": "Critic coefficient", "type": "number", "min": 0, "max": 20, "step": 0.1, "expert": True, "description": "Relative weight of the value-function loss."},
            {"path": "ppo.activation", "label": "Network activation", "type": "select", "options": ["elu", "relu", "tanh", "swish"], "expert": True, "description": "Nonlinearity used by the actor/critic multilayer perceptron."},
            {"path": "ppo.hidden_units.0", "label": "Hidden layer 1", "type": "integer", "min": 16, "max": 4096, "step": 16, "expert": True, "description": "Width of the first actor/critic hidden layer."},
            {"path": "ppo.hidden_units.1", "label": "Hidden layer 2", "type": "integer", "min": 16, "max": 4096, "step": 16, "expert": True, "description": "Width of the second actor/critic hidden layer."},
            {"path": "ppo.hidden_units.2", "label": "Hidden layer 3", "type": "integer", "min": 16, "max": 4096, "step": 16, "expert": True, "description": "Width of the third actor/critic hidden layer."},
        ],
    },
]


def get_path(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def set_path(document: dict[str, Any], path: str, value: Any) -> None:
    target: Any = document
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    leaf = parts[-1]
    if isinstance(target, list):
        target[int(leaf)] = value
    else:
        target[leaf] = value


def canonical_json(profile: dict[str, Any]) -> str:
    return json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def profile_hash(profile: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(profile).encode("utf-8")).hexdigest()


def _validate_field(field: dict[str, Any], profile: dict[str, Any], errors: list[str]) -> None:
    path = field["path"]
    try:
        value = get_path(profile, path)
    except (KeyError, IndexError, TypeError):
        errors.append(f"Missing setting: {path}")
        return
    kind = field["type"]
    if kind == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path} must be true or false.")
        return
    if kind == "select":
        if value not in field["options"]:
            errors.append(f"{path} must be one of: {', '.join(field['options'])}.")
        return
    if kind == "text":
        if not isinstance(value, str):
            errors.append(f"{path} must be text.")
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be numeric.")
        return
    if kind == "integer" and not isinstance(value, int):
        errors.append(f"{path} must be a whole number.")
    if "min" in field and value < field["min"]:
        errors.append(f"{path} must be at least {field['min']}.")
    if "max" in field and value > field["max"]:
        errors.append(f"{path} must be at most {field['max']}.")


def validate_profile(profile: object, *, for_launch: bool = False) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(profile, dict):
        return {"errors": ["Profile must be a JSON object."], "warnings": []}
    if profile.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}.")
    for key in ("profile_id", "display_name", "robot", "training"):
        if key not in profile:
            errors.append(f"Missing top-level setting: {key}")
    if errors:
        return {"errors": errors, "warnings": warnings}

    for group in FIELD_GROUPS:
        for field in group["fields"]:
            _validate_field(field, profile, errors)

    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in profile_id
    ):
        errors.append("profile_id may contain only letters, numbers, hyphens, and underscores.")

    robot = profile.get("robot", {})
    joints = robot.get("joints", []) if isinstance(robot, dict) else []
    expected = robot.get("expected_joint_count") if isinstance(robot, dict) else None
    if expected != 12:
        errors.append("robot.expected_joint_count must be exactly 12 for this quadruped control center.")
    if not isinstance(joints, list) or len(joints) != expected:
        errors.append("robot.joints must contain exactly expected_joint_count entries.")
    else:
        names: set[str] = set()
        semantics: set[str] = set()
        for index, joint in enumerate(joints):
            if not isinstance(joint, dict):
                errors.append(f"robot.joints[{index}] must be an object.")
                continue
            name = joint.get("name")
            semantic = joint.get("semantic")
            if not isinstance(name, str) or not name:
                errors.append(f"robot.joints[{index}].name is required.")
            elif name in names:
                errors.append(f"Duplicate joint name: {name}")
            else:
                names.add(name)
            if not isinstance(semantic, str) or not semantic:
                errors.append(f"robot.joints[{index}].semantic is required.")
            elif semantic in semantics:
                errors.append(f"Duplicate semantic joint: {semantic}")
            else:
                semantics.add(semantic)
            for field_name in ("rest_position", "stiffness", "damping", "effort_limit", "velocity_limit", "armature"):
                value = joint.get(field_name)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"robot.joints[{index}].{field_name} must be numeric.")
            if joint.get("direction") not in (-1, 1):
                errors.append(f"robot.joints[{index}].direction must be -1 or 1.")

    environment = profile.get("environment", {})
    stance_action = environment.get("stationary_stance_action")
    if (
        not isinstance(stance_action, list)
        or len(stance_action) != expected
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -1.0 <= value <= 1.0
            for value in stance_action
        )
    ):
        errors.append(
            "environment.stationary_stance_action must contain 12 finite normalized values within [-1, 1]."
        )

    asset_source = robot.get("asset_source", "") if isinstance(robot, dict) else ""
    asset = robot.get("asset_usd", "") if isinstance(robot, dict) else ""
    if asset_source == "Isaac Lab built-in":
        if asset != "isaaclab://Robots/Unitree/Go2/go2.usd":
            errors.append("The built-in reference asset must be the installed Isaac Lab Unitree Go2 USD.")
        if robot.get("reference_task") != "Isaac-Velocity-Flat-Unitree-Go2-v0":
            errors.append("The built-in reference task must be Isaac-Velocity-Flat-Unitree-Go2-v0.")
    elif asset_source == "Workspace USD":
        if not isinstance(asset, str) or not asset.startswith("/workspace/projects/assets/onshape/"):
            errors.append("A workspace robot.asset_usd must be below /workspace/projects/assets/onshape/.")
        elif PurePosixPath(asset).name not in ("robot.usda", "robot.usd"):
            warnings.append("Custom robot assets normally use robot.usda or robot.usd as their stable entry layer.")
    else:
        errors.append("robot.asset_source must be Isaac Lab built-in or Workspace USD.")

    forward_axis = robot.get("forward_axis", [])
    if (
        not isinstance(forward_axis, list)
        or len(forward_axis) != 3
        or any(isinstance(component, bool) or not isinstance(component, (int, float)) for component in forward_axis)
    ):
        errors.append("robot.forward_axis must contain three numeric components.")
    elif abs(forward_axis[2]) > 1e-9 or forward_axis[0] ** 2 + forward_axis[1] ** 2 < 1e-9:
        errors.append("robot.forward_axis must be a non-zero direction in the body XY plane.")

    placeholder_paths: list[str] = []
    if isinstance(asset, str) and "replace-me" in asset.lower():
        placeholder_paths.append("robot.asset_usd")
    for index, joint in enumerate(joints if isinstance(joints, list) else []):
        if isinstance(joint, dict) and isinstance(joint.get("name"), str) and "REPLACE_" in joint["name"].upper():
            placeholder_paths.append(f"robot.joints[{index}].name")

    contacts = robot.get("contacts", {}) if isinstance(robot, dict) else {}
    feet = contacts.get("feet", {}) if isinstance(contacts, dict) else {}
    if set(feet) != {"front_right", "front_left", "back_right", "back_left"}:
        errors.append("robot.contacts.feet must map all four semantic feet.")
    elif any(not isinstance(value, str) or not value for value in feet.values()):
        errors.append("Every semantic foot must have a non-empty USD link expression.")
    for key in ("base", "undesired"):
        if not isinstance(contacts.get(key), str) or not contacts.get(key):
            errors.append(f"robot.contacts.{key} is required.")
        elif "REPLACE_" in contacts[key].upper():
            placeholder_paths.append(f"robot.contacts.{key}")
    if isinstance(feet, dict):
        for foot, value in feet.items():
            if isinstance(value, str) and "REPLACE_" in value.upper():
                placeholder_paths.append(f"robot.contacts.feet.{foot}")

    if expected == 12 and set(SEMANTIC_JOINTS_12) != semantics:
        errors.append("A 12-DOF quadruped must map hip abduction, hip flexion, and knee flexion for every leg.")
    if profile["commands"]["forward_min"] > profile["commands"]["forward_max"]:
        errors.append("commands.forward_min cannot exceed commands.forward_max.")
    if profile["commands"]["lateral_min"] > profile["commands"]["lateral_max"]:
        errors.append("commands.lateral_min cannot exceed commands.lateral_max.")
    if profile["commands"]["hold_min_s"] > profile["commands"]["hold_max_s"]:
        errors.append("commands.hold_min_s cannot exceed commands.hold_max_s.")
    if profile["commands"]["turn_yaw_min"] > profile["commands"]["turn_yaw_max"]:
        errors.append("commands.turn_yaw_min cannot exceed commands.turn_yaw_max.")
    if profile["commands"]["standing_fraction"] + profile["commands"]["turn_fraction"] > 1:
        errors.append("Standing and turn-in-place command shares cannot total more than 1.")
    if not 0 <= profile["commands"].get("turn_right_fraction", 0.5) <= 1:
        errors.append("commands.turn_right_fraction must be between 0 and 1.")
    if not 0 <= profile["commands"].get("curve_right_fraction", 0.5) <= 1:
        errors.append("commands.curve_right_fraction must be between 0 and 1.")
    if profile["reset"]["small_tilt_deg"] > profile["reset"]["large_tilt_deg"]:
        errors.append("reset.small_tilt_deg cannot exceed reset.large_tilt_deg.")
    randomization = profile["domain_randomization"]
    for minimum, maximum, label in (
        ("base_mass_scale_min", "base_mass_scale_max", "base mass scale"),
        ("link_mass_scale_min", "link_mass_scale_max", "link mass scale"),
        ("actuator_drive_scale_min", "actuator_drive_scale_max", "actuator drive scale"),
        ("actuator_effort_scale_min", "actuator_effort_scale_max", "actuator effort scale"),
        ("actuator_velocity_scale_min", "actuator_velocity_scale_max", "actuator velocity scale"),
        ("robot_static_friction_min", "robot_static_friction_max", "robot static friction"),
        ("robot_dynamic_friction_min", "robot_dynamic_friction_max", "robot dynamic friction"),
        ("robot_restitution_min", "robot_restitution_max", "robot restitution"),
    ):
        if randomization[minimum] > randomization[maximum]:
            errors.append(
                f"domain_randomization {label} minimum cannot exceed its maximum."
            )
    if profile["disturbance"]["push_interval_min_s"] > profile["disturbance"]["push_interval_max_s"]:
        errors.append("disturbance.push_interval_min_s cannot exceed push_interval_max_s.")
    if profile["terrain"]["roughness_min"] > profile["terrain"]["roughness_max"]:
        errors.append("terrain.roughness_min cannot exceed terrain.roughness_max.")
    stage = profile["training"]["stage"]
    surface = profile["environment"]["surface"]
    if stage in ("V2Goal", "V2Rough"):
        mobility_requirements = (
            ("commands.forward_min", profile["commands"]["forward_min"], -0.18, "at most"),
            ("commands.forward_max", profile["commands"]["forward_max"], 0.22, "at least"),
            ("commands.lateral_min", profile["commands"]["lateral_min"], -0.16, "at most"),
            ("commands.lateral_max", profile["commands"]["lateral_max"], 0.16, "at least"),
            ("commands.yaw_max", profile["commands"]["yaw_max"], 0.25, "at least"),
        )
        for path, value, required, comparison in mobility_requirements:
            invalid = value > required if comparison == "at most" else value < required
            if invalid:
                errors.append(
                    f"{stage} requires {path} to be {comparison} {required} for the fixed full-mobility promotion screen."
                )
    if stage != "V2Rough" and surface != "Flat":
        errors.append(f"{stage} is a flat-ground acceptance stage; select Flat or switch to V2Rough.")
    if stage == "V2Rough" and surface == "Flat":
        warnings.append("V2Rough currently selects a flat surface; use V2Core unless this is an intentional regression check.")
    if profile["environment"]["dynamic_friction"] > profile["environment"]["static_friction"]:
        warnings.append("Dynamic friction is usually no greater than static friction.")
    physics_hz = profile["environment"]["physics_hz"]
    control_hz = profile["environment"]["control_hz"]
    ratio = physics_hz / control_hz
    if abs(ratio - round(ratio)) > 1e-9:
        errors.append("environment.control_hz must divide environment.physics_hz exactly.")
    batch = profile["training"]["num_envs"] * profile["ppo"]["horizon_length"]
    if batch % profile["ppo"]["minibatch_size"]:
        errors.append("training.num_envs × ppo.horizon_length must divide evenly by ppo.minibatch_size.")
    checkpoint = profile["training"]["checkpoint"]
    profile_key = profile_id.replace("-", "_") if isinstance(profile_id, str) else "invalid"
    experiment_name = "quadruped_v2_" + profile_key
    checkpoint_root = f"/workspace/projects/training/logs/rl_games/{experiment_name}/"
    if checkpoint and not (checkpoint.startswith(checkpoint_root) and checkpoint.endswith(".pth")):
        errors.append(
            "training.checkpoint must come from this same 12-DOF robot profile: "
            f"{checkpoint_root}.../.pth"
        )
    if stage in ("V2Robust", "V2Goal") and not checkpoint:
        errors.append(f"{stage} requires a passing V2 checkpoint.")
    if stage == "V2Rough" and not checkpoint:
        warnings.append("A fresh V2 Rough run is exploratory; normal progression continues from a passing Goal checkpoint.")
    if robot.get("semantic_direction_contract") == "inward-knee-diagonal-v1":
        required_directions = {
            "front_right_hip_abduction": 1,
            "front_right_hip_flexion": 1,
            "front_right_knee_flexion": -1,
            "front_left_hip_abduction": -1,
            "front_left_hip_flexion": -1,
            "front_left_knee_flexion": 1,
            "back_right_hip_abduction": -1,
            "back_right_hip_flexion": -1,
            "back_right_knee_flexion": 1,
            "back_left_hip_abduction": 1,
            "back_left_hip_flexion": 1,
            "back_left_knee_flexion": -1,
        }
        actual_directions = {
            joint.get("semantic"): joint.get("direction")
            for joint in robot.get("joints", ())
        }
        mismatched = [
            semantic
            for semantic, direction in required_directions.items()
            if actual_directions.get(semantic) != direction
        ]
        if mismatched:
            errors.append(
                "The inward-knee semantic direction contract is invalid for: "
                + ", ".join(mismatched)
                + ". Run inspect_profile_kinematics.py before changing these signs."
            )
    if placeholder_paths:
        message = "Replace template values before launch: " + ", ".join(placeholder_paths) + "."
        if for_launch:
            errors.append(message)
        else:
            warnings.append(message)
    if for_launch and not robot.get("ready_for_training", False):
        errors.append("The robot is not marked ready for training. Validate its USD graph, contacts, joint map, and standing pose first.")
    return {"errors": errors, "warnings": warnings}


def normalized_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a detached profile with joint defaults resolved explicitly."""
    result = deepcopy(profile)
    defaults = result["actuators"]
    for joint in result["robot"]["joints"]:
        for key in ("stiffness", "damping", "effort_limit", "velocity_limit", "armature"):
            if joint.get(key) is None:
                joint[key] = defaults[key]
    return result


def load_profile(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        profile = json.load(handle)
    result = validate_profile(profile)
    if result["errors"]:
        raise ValueError("Invalid control profile: " + " ".join(result["errors"]))
    return normalized_profile(profile)


def save_profile(path: Path, profile: dict[str, Any]) -> None:
    result = validate_profile(profile)
    if result["errors"]:
        raise ValueError("Invalid control profile: " + " ".join(result["errors"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)

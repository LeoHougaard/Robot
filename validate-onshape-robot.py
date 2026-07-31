"""Open an Onshape Publisher USD in Isaac Sim and validate its robot graph."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import traceback

from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--asset", required=True, help="Absolute container path to robot.usda")
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

app_launcher = AppLauncher(args)
simulation_app = app_launcher.app

try:
    import omni.timeline
    import omni.usd
    from pxr import Usd, UsdPhysics

    asset = Path(args.asset)
    if not asset.is_absolute() or asset.name != "robot.usda" or not asset.is_file():
        raise RuntimeError(f"Expected an existing absolute robot.usda path, got: {asset}")

    context = omni.usd.get_context()
    print(f"opening_asset={asset}", flush=True)
    stage = Usd.Stage.Open(str(asset))
    if stage is None:
        raise RuntimeError("Isaac Sim could not open the Publisher USD.")
    print(f"opened_default_prim={stage.GetDefaultPrim().GetPath()}", flush=True)

    revolute_joints = []
    rigid_bodies = []
    collisions = []
    robot_roots = []
    broken_joint_targets = []

    for prim in stage.Traverse():
        if prim.IsA(UsdPhysics.RevoluteJoint):
            revolute_joints.append(prim)
            joint = UsdPhysics.Joint(prim)
            for relation_name, relation in (
                ("body0", joint.GetBody0Rel()),
                ("body1", joint.GetBody1Rel()),
            ):
                targets = relation.GetTargets()
                if len(targets) != 1 or not stage.GetPrimAtPath(targets[0]).IsValid():
                    broken_joint_targets.append(f"{prim.GetPath()}:{relation_name}")
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_bodies.append(prim)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            collisions.append(prim)
        applied_schemas = [str(schema) for schema in prim.GetAppliedSchemas()]
        authored_api_schemas = str(prim.GetMetadata("apiSchemas") or "")
        if "IsaacRobotAPI" in applied_schemas or "IsaacRobotAPI" in authored_api_schemas:
            robot_roots.append(prim)

    if not robot_roots:
        raise RuntimeError("No IsaacRobotAPI root was found.")
    if not revolute_joints:
        raise RuntimeError("No revolute joints were found.")
    if broken_joint_targets:
        raise RuntimeError(
            "Broken joint body relationships: " + ", ".join(broken_joint_targets)
        )
    if len(rigid_bodies) < len(revolute_joints) + 1:
        raise RuntimeError(
            f"Found {len(rigid_bodies)} rigid bodies for "
            f"{len(revolute_joints)} revolute joints."
        )
    if not collisions:
        raise RuntimeError("No collision-enabled prims were found.")
    print("robot_graph_assertions=PASS", flush=True)

    # Reference the validated asset into the application's disposable runtime
    # stage and play a few headless frames so PhysX consumes the composition.
    runtime_stage = context.get_stage()
    if runtime_stage is None:
        context.new_stage()
        simulation_app.update()
        runtime_stage = context.get_stage()
    runtime_stage.DefinePrim("/World", "Xform")
    robot_prim = runtime_stage.DefinePrim("/World/Robot", "Xform")
    robot_prim.GetReferences().AddReference(str(asset))
    if not any(prim.IsA(UsdPhysics.Scene) for prim in runtime_stage.Traverse()):
        UsdPhysics.Scene.Define(runtime_stage, "/PhysicsScene")
    for _ in range(3):
        simulation_app.update()
    timeline = omni.timeline.get_timeline_interface()
    timeline.play()
    for _ in range(12):
        simulation_app.update()
    timeline.stop()
    print("physx_frames=12", flush=True)

    used_layers = [layer.identifier for layer in stage.GetUsedLayers()]
    print("ONSHAPE_ROBOT_VALIDATION=PASS", flush=True)
    print(f"asset={asset}", flush=True)
    print(f"default_prim={stage.GetDefaultPrim().GetPath()}", flush=True)
    print(f"isaac_robot_roots={len(robot_roots)}", flush=True)
    print(f"revolute_joints={len(revolute_joints)}", flush=True)
    print(f"rigid_bodies={len(rigid_bodies)}", flush=True)
    print(f"collision_prims={len(collisions)}", flush=True)
    print(f"composed_layers={len(used_layers)}", flush=True)
except Exception:
    print("ONSHAPE_ROBOT_VALIDATION=FAIL", file=sys.stderr, flush=True)
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    # Avoid SimulationApp.close() masking the validator's non-zero status.
    os._exit(1)
finally:
    simulation_app.close()

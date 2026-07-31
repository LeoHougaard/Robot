"""Validate the composed visual review scene without launching another Kit app."""

from __future__ import annotations

import sys

from pxr import Usd, UsdGeom, UsdPhysics, UsdShade


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


scene_path = sys.argv[1]
stage = Usd.Stage.Open(scene_path)
require(stage is not None, f"Could not open stage: {scene_path}")

robot = stage.GetPrimAtPath("/World/Robot")
ground = stage.GetPrimAtPath("/World/GroundPlane")
physics_scene = stage.GetPrimAtPath("/World/PhysicsScene")
require(robot.IsValid(), "Missing /World/Robot")
require(ground.IsValid(), "Missing /World/GroundPlane")
require(physics_scene.IsValid(), "Missing /World/PhysicsScene")
require(robot.HasAPI(UsdPhysics.ArticulationRootAPI), "Robot is not an articulation root")
require(ground.HasAPI(UsdPhysics.CollisionAPI), "Ground is missing collision")
require(physics_scene.IsA(UsdPhysics.Scene), "PhysicsScene has the wrong schema")

joints = [prim for prim in Usd.PrimRange(robot) if prim.IsA(UsdPhysics.RevoluteJoint)]
rigid_bodies = [prim for prim in Usd.PrimRange(robot) if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
meshes = [prim for prim in Usd.PrimRange(robot) if prim.IsA(UsdGeom.Mesh)]
require(len(joints) == 8, f"Expected 8 revolute joints, found {len(joints)}")
require(len(rigid_bodies) == 9, f"Expected 9 rigid bodies, found {len(rigid_bodies)}")

robot_material, _ = UsdShade.MaterialBindingAPI(robot).ComputeBoundMaterial()
ground_material, _ = UsdShade.MaterialBindingAPI(ground).ComputeBoundMaterial()
require(robot_material, "Robot material did not resolve")
require(ground_material, "Ground material did not resolve")
require(
    robot_material.GetPath() == "/World/Looks/RobotOrange",
    f"Unexpected robot material: {robot_material.GetPath()}",
)
require(
    ground_material.GetPath() == "/World/Looks/GroundDarkGray",
    f"Unexpected ground material: {ground_material.GetPath()}",
)

print(f"scene={scene_path}")
print(f"robot={robot.GetPath()} joints={len(joints)} rigid_bodies={len(rigid_bodies)}")
print(f"standalone_usd_meshes={len(meshes)} (glTF composition is provided by the full Kit app)")
print(f"robot_material={robot_material.GetPath()}")
print(f"ground={ground.GetPath()} collision=true material={ground_material.GetPath()}")
print("validation=passed")

"""Create a separate convex-collision overlay without changing robot geometry.

All joint definitions, masses, inertias, transforms and visible meshes remain
referenced from the source asset. The original SDF asset stays available for
matched fidelity checks. Convex hulls fill mesh concavities and must be reviewed
on the actual contact surfaces before using the result for policy promotion.
"""
import argparse
import hashlib
import json
from pathlib import Path

from pxr import Usd, UsdPhysics


def create(source, output):
    original = Usd.Stage.Open(str(source))
    default = original.GetDefaultPrim()
    if not default:
        raise ValueError("source asset has no default prim")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    stage = Usd.Stage.CreateNew(str(output))
    for name in ("metersPerUnit", "upAxis"):
        if original.HasAuthoredMetadata(name):
            stage.SetMetadata(name, original.GetMetadata(name))
    root = stage.DefinePrim(default.GetPath())
    root.GetReferences().AddReference(str(source), default.GetPath())
    stage.SetDefaultPrim(root)
    changed = []
    for prim in Usd.PrimRange(default):
        if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
            continue
        api = UsdPhysics.MeshCollisionAPI(prim)
        previous = api.GetApproximationAttr().Get()
        if previous != "sdf":
            continue
        override = stage.OverridePrim(prim.GetPath())
        UsdPhysics.MeshCollisionAPI.Apply(override).GetApproximationAttr().Set("convexHull")
        changed.append(str(prim.GetPath()))
    if len(changed) != 29:
        raise ValueError(f"expected the reviewed robot's 29 SDF collision roots, found {len(changed)}")
    stage.GetRootLayer().Save()
    # Exhaustively compare composed authored attribute values. No mass,
    # inertia, joint or transform change is hidden in the asset conversion.
    changes = []
    for prim in Usd.PrimRange(default):
        actual = stage.GetPrimAtPath(prim.GetPath())
        for attr in prim.GetAttributes():
            other = actual.GetAttribute(attr.GetName())
            if str(attr.Get()) != str(other.Get()):
                changes.append(str(attr.GetPath()))
    expected = sorted(path + ".physics:approximation" for path in changed)
    if sorted(changes) != expected:
        raise ValueError(f"unexpected composed attribute changes: {changes}")
    report = dict(source=str(source), output=str(output), changed_collision_roots=changed,
                  unchanged_attribute_check=True,
                  source_layers={layer.realPath: hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()
                                 for layer in original.GetUsedLayers() if layer.realPath},
                  output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    output.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"changed_colliders": len(changed), "attribute_check": True, "output_sha256": report["output_sha256"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create(args.source, args.output)

"""Pure USD geometry helpers for material-point foot diagnostics."""
import numpy as np
from pxr import Usd, UsdGeom


def sole_points(asset, body_names, up_axis):
    """Return one lowest collision-sole material point per body, in link frames."""
    stage = Usd.Stage.Open(asset)
    root = stage.GetDefaultPrim()
    if not root:
        raise ValueError(f"USD asset has no default prim: {asset}")
    up_axis = np.asarray(up_axis, dtype=float)
    up_axis /= np.linalg.norm(up_axis)
    cache = UsdGeom.XformCache()
    result = []
    for name in body_names:
        link = stage.GetPrimAtPath(root.GetPath().AppendPath("links/" + name))
        if not link:
            raise ValueError(f"missing foot link {name}")
        coordinates, heights = [], []
        for prim in Usd.PrimRange(link):
            if not prim.IsA(UsdGeom.Mesh) or "/collisions/" not in str(prim.GetPath()):
                continue
            points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
            if points.size == 0:
                continue
            homogeneous = np.column_stack((points, np.ones(len(points))))
            coordinates.append((homogeneous @ np.asarray(cache.ComputeRelativeTransform(prim, link)[0]))[:, :3])
            in_root = homogeneous @ np.asarray(cache.ComputeRelativeTransform(prim, root)[0])
            heights.append(in_root[:, :3] @ up_axis)
        if not coordinates:
            raise ValueError(f"no collision mesh points for {name}")
        coordinates, heights = np.concatenate(coordinates), np.concatenate(heights)
        result.append(coordinates[heights <= heights.min() + .0005].mean(axis=0))
    return np.asarray(result)

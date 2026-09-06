"""CPU checks for the fixed 64-column varied-terrain mixture."""
from __future__ import annotations

import math
import numpy as np

from delivery_terrain import terrain_for_varied


def _mesh_data(sub, size=(8.0, 8.0)):
    sub.size = size
    return sub.function(0.0, sub)


def _top_heights(meshes, positive_only=False):
    values = []
    for mesh in meshes:
        # Upward faces only: including downward underside faces duplicates the
        # base level and makes riser differences look incorrect.
        horizontal = mesh.face_normals[:, 2] > .999999
        faces = mesh.faces[horizontal]
        if len(faces):
            values.extend(mesh.vertices[faces, 2].max(axis=1))
    values = np.unique(np.round(values, 9))
    return values[values > 1e-8] if positive_only else values


def verify_geometry():
    cfg = terrain_for_varied()
    sub = cfg.terrain_generator.sub_terrains
    assert cfg.terrain_generator.curriculum is True
    assert cfg.terrain_generator.size == (8.0, 8.0)
    assert cfg.terrain_generator.num_rows == 1
    assert cfg.terrain_generator.num_cols == 64
    assert len(sub) == 45
    assert math.isclose(sub["floor"].proportion, 20.0 / 64.0)
    assert sum(math.isclose(v.proportion, 1.0 / 64.0) for k, v in sub.items() if k != "floor") == 44

    bump_ranges = []
    for index in range(16):
        bump_meshes, _ = _mesh_data(sub[f"bumps_{index:02d}"])
        bump_tops = _top_heights(bump_meshes, positive_only=True)
        assert bump_tops.min() >= .0025 - 1e-7
        assert bump_tops.max() <= .006 + 1e-7
        bump_ranges.append((float(bump_tops.min()), float(bump_tops.max())))

    for name, expected in (("slope_ascend_25", .015), ("slope_ascend_50", .03),
                           ("slope_descend_25", .015), ("slope_descend_50", .03)):
        meshes, _ = _mesh_data(sub[name])
        vertices = np.concatenate([m.vertices for m in meshes])
        assert np.ptp(vertices[:, 2]) <= expected * 8.0 + 1e-6
        assert np.ptp(vertices[:, 2]) > 0.0
        radius = np.linalg.norm(vertices[:, :2] - 4.0, axis=1)
        trend = np.polyfit(radius, vertices[:, 2], 1)[0]
        assert (trend > 0.0) == ("ascend" in name)

    stair_names = [k for k in sub if k.startswith("stairs_")]
    assert len(stair_names) == 24
    for name in stair_names:
        parts = name.split("_")
        height = int(parts[2][:-2]) / 1000.0
        meshes, _ = _mesh_data(sub[name])
        levels = _top_heights(meshes)
        assert len(levels) >= 2, name
        assert np.allclose(np.diff(levels), height, atol=1e-8, rtol=0.0)
    return {"passed": True, "bump_top_ranges_m": bump_ranges, "stair_cases": len(stair_names)}


def verify_columns():
    cfg = terrain_for_varied()
    names = list(cfg.terrain_generator.sub_terrains)
    proportions = np.cumsum(np.asarray([cfg.terrain_generator.sub_terrains[n].proportion for n in names]))
    assignments = [int(np.where(column / 64.0 + .001 < proportions / proportions[-1])[0][0]) for column in range(64)]
    counts = np.bincount(assignments, minlength=len(names))
    assert counts[0] == 20
    assert all(counts[1:17] == 1)
    assert all(counts[17:21] == 1)
    assert all(counts[21:] == 1)
    return {"passed": True, "columns": 64, "flat": 20, "bumps": 16, "slopes": 4, "stairs": 24}


def verify():
    return {"geometry": verify_geometry(), "columns": verify_columns()}


if __name__ == "__main__":
    print(verify())

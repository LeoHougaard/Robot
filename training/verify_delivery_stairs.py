"""CPU checks for the fixed mesh stair terrain contract."""
import math

import numpy as np

from delivery_terrain import (
    STAIR_EVAL_TREAD_WIDTHS_MM,
    STAIR_HEIGHTS_MM,
    STAIR_PLATFORM_WIDTH_M,
    STAIR_TREAD_WIDTHS_MM,
    terrain_for_stairs,
)


def _top_levels(meshes):
    levels = []
    for mesh in meshes:
        normals = mesh.face_normals
        faces = mesh.faces[normals[:, 2] > .999999]
        if len(faces):
            levels.extend(np.unique(np.round(mesh.vertices[faces, 2].max(axis=1), 9)))
    return np.unique(np.round(levels, 9))


def _radial_levels(meshes, center, tolerance):
    values = []
    for mesh in meshes:
        normals = mesh.face_normals
        faces = mesh.faces[normals[:, 2] > .999999]
        if not len(faces):
            continue
        vertices = mesh.vertices[faces]
        centroids = vertices.mean(axis=1)
        for centroid in centroids:
            if abs(centroid[1] - center) <= tolerance:
                values.append((round(abs(float(centroid[0] - center)), 5), float(centroid[2])))
    grouped = {}
    for radius, height in values:
        grouped.setdefault(radius, []).append(height)
    return [(radius, float(np.median(grouped[radius]))) for radius in sorted(grouped)]


def verify_stair_geometry(kind, height_mm, tread_width_mm):
    cfg = terrain_for_stairs(kind, height_mm, tread_width_mm)
    sub = next(iter(cfg.terrain_generator.sub_terrains.values()))
    sub.size = cfg.terrain_generator.size
    meshes, origin = sub.function(.0, sub)
    vertices = np.concatenate([mesh.vertices for mesh in meshes])
    normals = np.concatenate([mesh.face_normals for mesh in meshes])
    vertical = int((np.abs(normals[:, 2]) < 1e-7).sum())
    horizontal = int((np.abs(np.abs(normals[:, 2]) - 1.) < 1e-7).sum())
    assert vertical > 0 and horizontal > 0
    levels = _top_levels(meshes)
    expected = height_mm / 1000.
    np.testing.assert_allclose(np.diff(levels), expected, atol=1e-8, rtol=0.)
    center = cfg.terrain_generator.size[0] / 2.
    radial = _radial_levels(meshes, center, tread_width_mm / 1000.)
    assert len(radial) >= 2
    radial_heights = np.asarray([height for _, height in radial])
    if kind == "stairs_ascend":
        assert np.all(np.diff(radial_heights) >= -1e-8)
    else:
        assert np.all(np.diff(radial_heights) <= 1e-8)
    middle = meshes[-1]
    top = middle.vertices[middle.vertices[:, 2] >= middle.vertices[:, 2].max() - 1e-8]
    support_width = min(np.ptp(top[:, 0]), np.ptp(top[:, 1]))
    assert abs(support_width - STAIR_PLATFORM_WIDTH_M) < 1e-5
    assert abs(float(origin[0]) - center) < 1e-8
    assert abs(float(origin[1]) - center) < 1e-8
    center_top = float(middle.vertices[:, 2].max())
    assert abs(float(origin[2]) - center_top) < 1e-8
    assert vertices.shape[1] == 3
    return {
        "kind": kind,
        "height_mm": height_mm,
        "tread_width_mm": tread_width_mm,
        "vertical_faces": vertical,
        "horizontal_faces": horizontal,
        "support_width_m": float(support_width),
        "origin_m": np.asarray(origin, dtype=float).tolist(),
        "top_levels_m": levels.tolist(),
    }


def verify_mixture():
    cfg = terrain_for_stairs()
    generator = cfg.terrain_generator
    assert generator.curriculum is True
    assert generator.num_rows == 1 and generator.num_cols == 32
    assert len(generator.sub_terrains) == 25
    assert math.isclose(generator.sub_terrains["floor"].proportion, .25)
    stair_proportions = [sub.proportion for name, sub in generator.sub_terrains.items()
                         if name != "floor"]
    assert len(stair_proportions) == 24
    assert all(math.isclose(proportion, 1. / 32.) for proportion in stair_proportions)
    proportions = np.asarray([sub.proportion for sub in generator.sub_terrains.values()])
    cumulative = np.cumsum(proportions / proportions.sum())
    # Match the installed TerrainGenerator's deterministic column selection.
    assignments = [int(np.where(column / 32. + .001 < cumulative)[0][0])
                   for column in range(32)]
    assert np.bincount(assignments).tolist() == [8] + [1] * 24
    return {
        "passed": True,
        "columns": generator.num_cols,
        "flat_columns": 8,
        "stair_columns": 24,
        "heights_mm": list(STAIR_HEIGHTS_MM),
        "tread_widths_mm": list(STAIR_TREAD_WIDTHS_MM),
    }


def verify():
    records = [verify_stair_geometry(kind, height, width)
               for kind in ("stairs_ascend", "stairs_descend")
               for height in STAIR_HEIGHTS_MM
               for width in STAIR_TREAD_WIDTHS_MM + STAIR_EVAL_TREAD_WIDTHS_MM]
    return {"passed": True, "mixture": verify_mixture(), "cases": records}


if __name__ == "__main__":
    print(verify())

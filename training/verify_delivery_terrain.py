"""Check real Isaac heightfield meshes at every proposed compression level."""
import numpy as np

from delivery_terrain import HEIGHT_FRACTIONS, terrain_for_height


def verify():
    flat = terrain_for_height(0.)
    assert flat.terrain_type == "plane" and flat.terrain_generator is None
    records = []
    full = terrain_for_height(1.).terrain_generator
    for name in ("small_uneven", "gentle_up", "gentle_down"):
        reference = None
        for fraction in reversed(HEIGHT_FRACTIONS[1:]):
            source = terrain_for_height(fraction).terrain_generator.sub_terrains[name]
            source.size = full.size
            source.horizontal_scale = full.horizontal_scale
            source.vertical_scale = full.vertical_scale
            source.slope_threshold = full.slope_threshold
            np.random.seed(5020)
            meshes, origin = source.function(.73, source)
            if reference is None:
                reference = ([m.copy() for m in meshes], origin.copy())
            assert len(meshes) == len(reference[0])
            for mesh, expected in zip(meshes, reference[0]):
                np.testing.assert_array_equal(mesh.faces, expected.faces)
                np.testing.assert_array_equal(mesh.vertices[:, :2], expected.vertices[:, :2])
                np.testing.assert_allclose(mesh.vertices[:, 2], expected.vertices[:, 2] * fraction, atol=1e-12)
            np.testing.assert_array_equal(origin[:2], reference[1][:2])
            np.testing.assert_allclose(origin[2], reference[1][2] * fraction, atol=1e-12)
            heights = np.concatenate([m.vertices[:, 2] for m in meshes])
            records.append(dict(terrain=name, fraction=fraction, minimum_z_m=float(heights.min()),
                                maximum_z_m=float(heights.max()), spawn_z_m=float(origin[2])))
    return dict(passed=True, plane_at_zero=True, xy_and_faces_unchanged=True, cases=records)

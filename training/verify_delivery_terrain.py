"""Check real Isaac heightfield meshes at every proposed compression level."""
import numpy as np

from delivery_terrain import (BUMP_HEIGHT_RANGE_M, HEIGHT_FRACTIONS,
                               terrain_for_height, terrain_with_2p5mm_bumps)


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


def verify_bumps25():
    """Verify the new bump range and preservation of floor/slopes."""
    old = terrain_for_height(.125, tile_size=8.).terrain_generator
    new = terrain_with_2p5mm_bumps(tile_size=8.).terrain_generator
    assert {name: cfg.proportion for name, cfg in new.sub_terrains.items()} == {
        "floor": .5, "small_uneven": .25, "gentle_up": .125, "gentle_down": .125,
    }
    records = {}
    for name in ("small_uneven", "gentle_up", "gentle_down"):
        old_cfg = old.sub_terrains[name]
        new_cfg = new.sub_terrains[name]
        for cfg in (old_cfg, new_cfg):
            cfg.size = old.size
            cfg.horizontal_scale = old.horizontal_scale
            cfg.vertical_scale = old.vertical_scale
            cfg.slope_threshold = old.slope_threshold
        np.random.seed(5020)
        old_meshes, old_origin = old_cfg.function(.73, old_cfg)
        np.random.seed(5020)
        new_meshes, new_origin = new_cfg.function(.73, new_cfg)
        old_z = np.concatenate([m.vertices[:, 2] for m in old_meshes])
        new_z = np.concatenate([m.vertices[:, 2] for m in new_meshes])
        if name == "small_uneven":
            positive = new_z[new_z > 1e-12]
            assert positive.min() >= BUMP_HEIGHT_RANGE_M[0] - 1e-9
            assert positive.max() <= BUMP_HEIGHT_RANGE_M[1] + 1e-9
            np.testing.assert_allclose([positive.min(), positive.max()],
                                       BUMP_HEIGHT_RANGE_M, atol=1e-9, rtol=0.)
            old_positive = old_z[old_z > 1e-12]
            np.testing.assert_allclose([old_positive.min(), old_positive.max()],
                                       [.000125, .00075], atol=1e-9, rtol=0.)
            assert np.any(new_z == 0.)
        else:
            np.testing.assert_array_equal(new_z, old_z)
            np.testing.assert_array_equal(new_origin, old_origin)
        records[name] = dict(z_min_m=float(new_z.min()), z_max_m=float(new_z.max()),
                             positive_min_m=(float(positive.min()) if name == "small_uneven" else None),
                             positive_max_m=(float(positive.max()) if name == "small_uneven" else None))
    return dict(passed=True, flat_fraction=.50, bump_range_m=list(BUMP_HEIGHT_RANGE_M), cases=records)

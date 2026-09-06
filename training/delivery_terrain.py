"""Compress terrain vertically without changing its horizontal geometry.

Isaac's random-uniform generator ignores difficulty. Generate its full-height
mesh first, then scale mesh and spawn-origin Z together. Do not shrink the
integer heightfield quantization step below its resolution.
"""
import math

import numpy as np


HEIGHT_FRACTIONS = (0., .125, .25, .5, .75, 1.)


def compress_meshes(meshes, origin, fraction):
    if not math.isfinite(fraction) or not 0. <= fraction <= 1.:
        raise ValueError("height fraction must be finite and in [0, 1]")
    result = []
    for original in meshes:
        mesh = original.copy()
        mesh.vertices[:, 2] *= fraction
        result.append(mesh)
    scaled_origin = np.array(origin, dtype=float, copy=True)
    scaled_origin[2] *= fraction
    return result, scaled_origin


def compressed_uniform(difficulty, cfg):
    from isaaclab.terrains.height_field.hf_terrains import random_uniform_terrain
    return compress_meshes(*random_uniform_terrain(1., cfg), cfg.height_fraction)


def compressed_slope(difficulty, cfg):
    from isaaclab.terrains.height_field.hf_terrains import pyramid_sloped_terrain
    return compress_meshes(*pyramid_sloped_terrain(1., cfg), cfg.height_fraction)


def terrain_for_height(fraction):
    """Explicit fixed stage. Advancement requires a separate evaluation decision."""
    import copy
    import isaaclab.terrains as terrain_gen
    from isaaclab.utils.configclass import configclass
    from simple_dog_task_current_body_v20.env_cfg import DeliveryFlatEvalCfg, _terrain

    if fraction not in HEIGHT_FRACTIONS:
        raise ValueError("height fraction is not a reviewed curriculum level")
    if fraction == 0.:
        return copy.deepcopy(DeliveryFlatEvalCfg().terrain)

    @configclass
    class UniformCfg(terrain_gen.HfRandomUniformTerrainCfg):
        function = compressed_uniform
        height_fraction = fraction

    @configclass
    class SlopeCfg(terrain_gen.HfPyramidSlopedTerrainCfg):
        function = compressed_slope
        height_fraction = fraction

    @configclass
    class DownCfg(terrain_gen.HfInvertedPyramidSlopedTerrainCfg):
        function = compressed_slope
        height_fraction = fraction

    terrain = copy.deepcopy(_terrain)
    generator = terrain.terrain_generator
    generator.num_rows = 1
    generator.difficulty_range = (1., 1.)
    generator.sub_terrains = {
        "floor": terrain_gen.MeshPlaneTerrainCfg(proportion=.50),
        "small_uneven": UniformCfg(proportion=.25, noise_range=(.001, .006), noise_step=.001, border_width=.2),
        "gentle_up": SlopeCfg(proportion=.125, slope_range=(.01, .06), platform_width=.8, border_width=.2),
        "gentle_down": DownCfg(proportion=.125, slope_range=(.01, .06), platform_width=.8, border_width=.2),
    }
    return terrain

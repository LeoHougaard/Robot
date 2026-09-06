"""Compress terrain vertically without changing its horizontal geometry.

Isaac's random-uniform generator ignores difficulty. Generate its full-height
mesh first, then scale mesh and spawn-origin Z together. Do not shrink the
integer heightfield quantization step below its resolution.
"""
import math

import numpy as np


HEIGHT_FRACTIONS = (0., .125, .25, .5, .75, 1.)
BUMP_HEIGHT_RANGE_M = (.0025, .006)
STAIR_HEIGHTS_MM = (6, 10, 15, 20)
STAIR_TREAD_WIDTHS_MM = (150, 200, 250)
STAIR_EVAL_TREAD_WIDTHS_MM = (175, 225)
STAIR_PLATFORM_WIDTH_M = .6


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


def terrain_for_height(fraction, terrain_kind="mixture", tile_size=4.0):
    """Explicit fixed stage. Advancement requires a separate evaluation decision."""
    import copy
    import isaaclab.terrains as terrain_gen
    from isaaclab.utils.configclass import configclass
    from simple_dog_task_current_body_v20.env_cfg import DeliveryFlatEvalCfg, _terrain

    if fraction not in HEIGHT_FRACTIONS:
        raise ValueError("height fraction is not a reviewed curriculum level")
    if terrain_kind not in ("mixture", "uniform", "up", "down"):
        raise ValueError("terrain kind must be mixture, uniform, up, or down")
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
    generator.size = (tile_size, tile_size)
    generator.num_rows = 1
    generator.num_cols = 8 if terrain_kind == "mixture" else 1
    generator.difficulty_range = (1., 1.)
    if terrain_kind == "mixture":
        generator.sub_terrains = {
            "floor": terrain_gen.MeshPlaneTerrainCfg(proportion=.50),
            "small_uneven": UniformCfg(proportion=.25, noise_range=(.001, .006), noise_step=.001, border_width=.2),
            "gentle_up": SlopeCfg(proportion=.125, slope_range=(.01, .06), platform_width=.8, border_width=.2),
            "gentle_down": DownCfg(proportion=.125, slope_range=(.01, .06), platform_width=.8, border_width=.2),
        }
    elif terrain_kind == "uniform":
        generator.sub_terrains = {"small_uneven": UniformCfg(proportion=1., noise_range=(.001, .006), noise_step=.001, border_width=.2)}
    elif terrain_kind == "up":
        generator.sub_terrains = {"gentle_up": SlopeCfg(proportion=1., slope_range=(.01, .06), platform_width=.8, border_width=.2)}
    else:
        generator.sub_terrains = {"gentle_down": DownCfg(proportion=1., slope_range=(.01, .06), platform_width=.8, border_width=.2)}
    return terrain


def terrain_with_2p5mm_bumps(terrain_kind="mixture", tile_size=8.0):
    """The reviewed next rough stage: 2.5--6 mm uniform bumps.

    The existing .125 stage and its separately scaled slopes remain the
    source geometry. Only the uniform subterrain is given the new integer
    heightfield range; floor and slope proportions are unchanged.
    """
    if terrain_kind not in ("mixture", "uniform", "up", "down"):
        raise ValueError("terrain kind must be mixture, uniform, up, or down")
    terrain = terrain_for_height(.125, terrain_kind, tile_size=tile_size)
    if terrain_kind in ("mixture", "uniform"):
        uniform = terrain.terrain_generator.sub_terrains["small_uneven"]
        # compress_meshes applies height_fraction=.5, so generate twice the
        # requested physical range before the vertical rescale.
        uniform.noise_range = tuple(2. * height for height in BUMP_HEIGHT_RANGE_M)
        uniform.noise_step = .001
        uniform.height_fraction = .5
    return terrain


def _stair_cfg(kind, height_mm, tread_width_mm, tile_size, proportion=1.):
    import isaaclab.terrains as terrain_gen

    if kind not in ("stairs_ascend", "stairs_descend"):
        raise ValueError("stair kind must be stairs_ascend or stairs_descend")
    step_width = tread_width_mm / 1000.
    step_height = height_mm / 1000.
    if tile_size <= STAIR_PLATFORM_WIDTH_M + 2. * step_width:
        raise ValueError("stair tile is too small for the requested tread")
    steps = math.floor((tile_size - STAIR_PLATFORM_WIDTH_M - 2. * step_width)
                       / (2. * step_width))
    if steps < 1:
        raise ValueError("stair tile has no complete riser")
    inner = STAIR_PLATFORM_WIDTH_M + 2. * steps * step_width
    border = (tile_size - inner) / 2.
    # Mesh generator computes floor((size - 2*border - platform)/(2*step))+1.
    # The epsilon keeps this at exactly `steps` despite floating point ties.
    platform = STAIR_PLATFORM_WIDTH_M + 1e-6
    cls = (terrain_gen.MeshInvertedPyramidStairsTerrainCfg
           if kind == "stairs_ascend" else terrain_gen.MeshPyramidStairsTerrainCfg)
    return cls(size=(tile_size, tile_size), proportion=proportion,
               step_height_range=(step_height, step_height),
               step_width=step_width, platform_width=platform,
               border_width=border, holes=False)


def terrain_for_stairs(kind="mixture", step_height_mm=6, step_width_mm=200,
                       tile_size=8.0):
    """Create the fixed 32-column stair mixture or one held-out stair kind."""
    import copy
    import isaaclab.terrains as terrain_gen
    from simple_dog_task_current_body_v20.env_cfg import _terrain

    if kind not in ("mixture", "stairs_ascend", "stairs_descend"):
        raise ValueError("stair kind must be mixture, stairs_ascend, or stairs_descend")
    if tile_size != 8.0:
        raise ValueError("stairs are reviewed only on 8 m tiles")
    if step_height_mm not in STAIR_HEIGHTS_MM:
        raise ValueError("stair height is not in the reviewed set")
    widths = STAIR_TREAD_WIDTHS_MM if kind == "mixture" else (step_width_mm,)
    if any(width not in STAIR_TREAD_WIDTHS_MM + STAIR_EVAL_TREAD_WIDTHS_MM for width in widths):
        raise ValueError("stair tread is not in the reviewed set")

    terrain = copy.deepcopy(_terrain)
    generator = terrain.terrain_generator
    generator.size = (tile_size, tile_size)
    generator.num_rows = 1
    generator.difficulty_range = (0., 0.)
    # Isaac's non-curriculum path samples columns randomly and can omit rare
    # stair cases. One row with fixed difficulty uses curriculum ordering only
    # to make the 32-column distribution deterministic, not to advance height.
    generator.curriculum = True
    if kind == "mixture":
        sub_terrains = {"floor": terrain_gen.MeshPlaneTerrainCfg(proportion=.25)}
        for direction in ("stairs_ascend", "stairs_descend"):
            for height in STAIR_HEIGHTS_MM:
                for width in STAIR_TREAD_WIDTHS_MM:
                    name = f"{direction}_{height}mm_{width}mm"
                    sub_terrains[name] = _stair_cfg(direction, height, width, tile_size,
                                                    proportion=1. / 32.)
        generator.num_cols = 32
    else:
        sub_terrains = {f"{kind}_{step_height_mm}mm_{step_width_mm}mm":
                        _stair_cfg(kind, step_height_mm, step_width_mm, tile_size)}
        generator.num_cols = 1
    generator.sub_terrains = sub_terrains
    return terrain


def terrain_for_varied(tile_size=8.0):
    """Build the fixed 64-column varied-terrain training mixture.

    Column assignment is deterministic through Isaac's curriculum ordering;
    the environment-level terrain curriculum remains disabled by the stage
    config.  The mixture is 20 flat, 16 bump, 4 slope, and 24 stair columns.
    """
    import copy
    import isaaclab.terrains as terrain_gen
    from simple_dog_task_current_body_v20.env_cfg import _terrain

    if tile_size != 8.0:
        raise ValueError("varied terrain is reviewed only on 8 m tiles")

    varied = copy.deepcopy(_terrain)
    generator = varied.terrain_generator
    generator.size = (tile_size, tile_size)
    generator.num_rows = 1
    generator.num_cols = 64
    generator.difficulty_range = (0.0, 0.0)
    generator.curriculum = True

    bumps = terrain_with_2p5mm_bumps(tile_size=tile_size)
    bump_cfg = bumps.terrain_generator.sub_terrains["small_uneven"]
    # The 8 m source generator uses a 2.5 mm vertical quantization, which
    # cannot represent the 1 mm reviewed bump step. Keep the explicit bump
    # range while using the existing 1 mm heightfield resolution.
    bump_cfg.vertical_scale = .001
    # The regular pyramid slopes down away from the center; the inverted
    # pyramid slopes up. Name the columns by physical travel direction.
    slope_ascend_quarter = terrain_for_height(.25, "down", tile_size).terrain_generator.sub_terrains["gentle_down"]
    slope_ascend_half = terrain_for_height(.5, "down", tile_size).terrain_generator.sub_terrains["gentle_down"]
    slope_descend_quarter = terrain_for_height(.25, "up", tile_size).terrain_generator.sub_terrains["gentle_up"]
    slope_descend_half = terrain_for_height(.5, "up", tile_size).terrain_generator.sub_terrains["gentle_up"]

    sub_terrains = {
        "floor": terrain_gen.MeshPlaneTerrainCfg(proportion=20.0 / 64.0),
    }
    for index in range(16):
        sub_terrains[f"bumps_{index:02d}"] = copy.deepcopy(bump_cfg)
        sub_terrains[f"bumps_{index:02d}"].proportion = 1.0 / 64.0
    for name, cfg in (("slope_ascend_25", slope_ascend_quarter),
                      ("slope_ascend_50", slope_ascend_half),
                      ("slope_descend_25", slope_descend_quarter),
                      ("slope_descend_50", slope_descend_half)):
        sub_terrains[name] = copy.deepcopy(cfg)
        sub_terrains[name].proportion = 1.0 / 64.0
    for direction in ("stairs_ascend", "stairs_descend"):
        for height in STAIR_HEIGHTS_MM:
            for width in STAIR_TREAD_WIDTHS_MM:
                name = f"{direction}_{height}mm_{width}mm"
                stair_cfg = terrain_for_stairs(direction, height, width, tile_size).terrain_generator.sub_terrains[name]
                sub_terrains[name] = copy.deepcopy(stair_cfg)
                sub_terrains[name].proportion = 1.0 / 64.0
    generator.sub_terrains = sub_terrains
    return varied

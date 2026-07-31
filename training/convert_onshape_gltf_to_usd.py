"""Convert Publisher glTF meshes to native USD for Isaac Lab's minimal app."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input-dir", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, required=True)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

app_launcher = AppLauncher(args)
simulation_app = app_launcher.app

try:
    import omni.kit.app

    extension_manager = omni.kit.app.get_app().get_extension_manager()
    extension_manager.set_extension_enabled_immediate("omni.kit.asset_converter", True)
    for _ in range(3):
        simulation_app.update()

    import omni.kit.asset_converter

    inputs = sorted(args.input_dir.glob("*.gltf"))
    if len(inputs) != 9:
        raise RuntimeError(f"Expected 9 Publisher glTF files, found {len(inputs)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    context = omni.kit.asset_converter.AssetConverterContext()
    settings = {
        "ignore_materials": True,
        "ignore_animations": True,
        "ignore_camera": True,
        "ignore_light": True,
        "single_mesh": True,
        "smooth_normals": True,
        "export_preview_surface": True,
        "use_meter_as_world_unit": True,
        "create_world_as_default_root_prim": False,
        "embed_textures": False,
    }
    for name, value in settings.items():
        if hasattr(context, name):
            setattr(context, name, value)

    async def convert_all() -> None:
        converter = omni.kit.asset_converter.get_instance()
        for input_path in inputs:
            output_path = args.output_dir / f"{input_path.stem}.usd"

            def progress(current: int, total: int, name: str = input_path.name) -> None:
                print(f"CONVERT_PROGRESS file={name} current={current} total={total}", flush=True)

            task = converter.create_converter_task(
                str(input_path),
                str(output_path),
                progress,
                context,
            )
            success = await task.wait_until_finished()
            if not success:
                raise RuntimeError(
                    f"Conversion failed for {input_path}: "
                    f"status={task.get_status()} error={task.get_error_message()}"
                )
            if not output_path.is_file():
                raise RuntimeError(f"Converter reported success but output is missing: {output_path}")
            print(f"CONVERTED input={input_path} output={output_path}", flush=True)

    future = asyncio.ensure_future(convert_all())
    while not future.done():
        simulation_app.update()
    future.result()
    print(f"CONVERSION_COMPLETE count={len(inputs)} output_dir={args.output_dir}", flush=True)
finally:
    simulation_app.close()

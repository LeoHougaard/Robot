"""Export the promoted RL-Games NumPy actor as a behavior-equivalent ONNX graph."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper, numpy_helper


def numpy_actor(observation: np.ndarray, arrays: dict[str, np.ndarray]) -> np.ndarray:
    value = (observation - arrays["obs_mean"]) / np.sqrt(arrays["obs_var"] + 1.0e-5)
    value = np.clip(value, -5.0, 5.0)
    for index in range(3):
        value = np.where(
            arrays[f"w{index}"] @ value + arrays[f"b{index}"] > 0.0,
            arrays[f"w{index}"] @ value + arrays[f"b{index}"],
            np.expm1(arrays[f"w{index}"] @ value + arrays[f"b{index}"]),
        )
    return np.clip(arrays["wout"] @ value + arrays["bout"], -1.0, 1.0).astype(np.float32)


def export(weights_path: Path, output_path: Path) -> None:
    arrays = {name: value.astype(np.float32) for name, value in np.load(weights_path).items()}
    expected_shapes = {
        "obs_mean": (180,), "obs_var": (180,),
        "w0": (128, 180), "b0": (128,),
        "w1": (128, 128), "b1": (128,),
        "w2": (128, 128), "b2": (128,),
        "wout": (12, 128), "bout": (12,),
    }
    actual_shapes = {name: value.shape for name, value in arrays.items()}
    if actual_shapes != expected_shapes:
        raise SystemExit(f"Unexpected actor shapes: {actual_shapes}")

    initializers = [
        numpy_helper.from_array(arrays["obs_mean"], "obs_mean"),
        numpy_helper.from_array(arrays["obs_var"], "obs_var"),
        numpy_helper.from_array(np.asarray(1.0e-5, dtype=np.float32), "epsilon"),
        numpy_helper.from_array(np.asarray(-5.0, dtype=np.float32), "norm_min"),
        numpy_helper.from_array(np.asarray(5.0, dtype=np.float32), "norm_max"),
        numpy_helper.from_array(np.asarray(-1.0, dtype=np.float32), "action_min"),
        numpy_helper.from_array(np.asarray(1.0, dtype=np.float32), "action_max"),
    ]
    for index in range(3):
        initializers.extend(
            (
                numpy_helper.from_array(arrays[f"w{index}"], f"w{index}"),
                numpy_helper.from_array(arrays[f"b{index}"], f"b{index}"),
            )
        )
    initializers.extend(
        (
            numpy_helper.from_array(arrays["wout"], "wout"),
            numpy_helper.from_array(arrays["bout"], "bout"),
        )
    )

    nodes = [
        helper.make_node("Sub", ["observation", "obs_mean"], ["centered"]),
        helper.make_node("Add", ["obs_var", "epsilon"], ["variance_eps"]),
        helper.make_node("Sqrt", ["variance_eps"], ["stddev"]),
        helper.make_node("Div", ["centered", "stddev"], ["normalized"]),
        helper.make_node("Clip", ["normalized", "norm_min", "norm_max"], ["clipped"]),
    ]
    current = "clipped"
    for index in range(3):
        dense = f"dense{index}"
        activated = f"elu{index}"
        nodes.append(
            helper.make_node(
                "Gemm",
                [current, f"w{index}", f"b{index}"],
                [dense],
                transB=1,
            )
        )
        nodes.append(helper.make_node("Elu", [dense], [activated], alpha=1.0))
        current = activated
    nodes.extend(
        (
            helper.make_node("Gemm", [current, "wout", "bout"], ["raw_action"], transB=1),
            helper.make_node("Clip", ["raw_action", "action_min", "action_max"], ["action"]),
        )
    )

    graph = helper.make_graph(
        nodes,
        "assembly-1-12dof-deterministic-actor",
        [helper.make_tensor_value_info("observation", TensorProto.FLOAT, [1, 180])],
        [helper.make_tensor_value_info("action", TensorProto.FLOAT, [1, 12])],
        initializer=initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="Pixel Robot tools/export_onnx.py",
        opset_imports=[helper.make_opsetid("", 17)],
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, output_path)

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    generator = np.random.default_rng(42)
    fixtures = []
    for _ in range(8):
        observation = generator.normal(size=180).astype(np.float32)
        expected = numpy_actor(observation, arrays)
        actual = session.run(["action"], {"observation": observation.reshape(1, -1)})[0][0]
        if not np.allclose(actual, expected, rtol=1.0e-5, atol=2.0e-6):
            raise SystemExit(f"ONNX parity failed: max error {np.max(np.abs(actual - expected))}")
        fixtures.append({"observation": observation.tolist(), "action": expected.tolist()})
    fixture_path = output_path.with_name("policy_reference.json")
    fixture_path.write_text(json.dumps({"rtol": 1.0e-5, "atol": 2.0e-6, "cases": fixtures}), encoding="utf-8")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path = output_path.with_name("policy_android_manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "assembly-1-12dof",
                "profile_sha256": "B25A4A05FA5A6439B82B824D2C2C826F2A9CC5AACC274D75EE8B4D39978035D3",
                "source_weights_sha256": digest(weights_path),
                "onnx_sha256": digest(output_path),
                "parity_cases": len(fixtures),
                "parity_rtol": 1.0e-5,
                "parity_atol": 2.0e-6,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {output_path}, manifest, and reference vectors; 8/8 CPU parity cases passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("weights", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.weights, args.output)


if __name__ == "__main__":
    main()

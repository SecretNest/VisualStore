"""A deterministic ONNX model small enough to build in a test: colour means → linear map."""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from visualstore.models.registry import ModelSpec


def build_tiny_model(path: Path, dim: int = 8) -> None:
    rng = np.random.default_rng(42)
    weights = numpy_helper.from_array(rng.normal(size=(3, dim)).astype(np.float32), name="W")
    inp = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, "H", "W"])
    out = helper.make_tensor_value_info("output", TensorProto.FLOAT, ["N", dim])
    nodes = [
        helper.make_node("ReduceMean", ["input"], ["means"], axes=[2, 3], keepdims=0),
        helper.make_node("MatMul", ["means", "W"], ["output"]),
    ]
    graph = helper.make_graph(nodes, "tiny", [inp], [out], initializer=[weights])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def tiny_spec(dim: int = 8) -> ModelSpec:
    return ModelSpec(
        id=f"tiny-test-{dim}-v1",
        url="",
        sha256="",
        dim=dim,
        input_size=16,
        mean=(0.0, 0.0, 0.0),
        std=(1.0, 1.0, 1.0),
        description="test model",
    )

"""One-off export of the candidate models to ONNX (ADR-0005). Needs torch + torchvision (CPU).

    uv run --no-project --index https://download.pytorch.org/whl/cpu \
        --with torch --with torchvision --with onnx --with onnxscript \
        python scripts/export_models.py --out /tmp/vs-models
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

import numpy as np
import torch

SSCD_URL = "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class Dinov2Cls(torch.nn.Module):
    def __init__(self, backbone: torch.nn.Module) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # CLS embedding, 384-d for ViT-S/14
        return self.backbone(x)


def export(model: torch.nn.Module, size: int, out: Path) -> None:
    model.eval()
    example = torch.randn(2, 3, size, size)
    torch.onnx.export(
        model,
        example,
        str(out),
        opset_version=17,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "N"}, "output": {0: "N"}},
        dynamo=False,
    )
    import onnxruntime as ort

    session = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    with torch.no_grad():
        expected = model(example).numpy()
    got = session.run(None, {"input": example.numpy()})[0]
    diff = float(np.abs(expected - got).max())
    if diff > 1e-3:
        sys.exit(f"{out.name}: ONNX output differs from PyTorch by {diff}")
    print(f"{out.name} sha256={sha256(out)} size={out.stat().st_size} max_abs_diff={diff:.2e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", choices=["sscd", "dinov2"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.only in (None, "sscd"):
        ckpt = args.out / "sscd_disc_mixup.torchscript.pt"
        if not ckpt.exists():
            urllib.request.urlretrieve(SSCD_URL, ckpt)  # noqa: S310
        sscd = torch.jit.load(str(ckpt), map_location="cpu")
        export(sscd, 288, args.out / "sscd-resnet50-512-v1.onnx")
    if args.only in (None, "dinov2"):
        backbone = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
        export(Dinov2Cls(backbone), 224, args.out / "dinov2-vits14-384-v1.onnx")


if __name__ == "__main__":
    main()

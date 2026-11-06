"""Print what the image can decode and prove the shipped models load. Runs during the image
build so the capability of each architecture is in the build log (design §14: heif/avif support
in arm64 libvips) and a broken model artefact fails the build instead of the first scan."""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pyvips

from visualstore.models.infer import Session
from visualstore.models.registry import load_registry, resolve_model_path

print(f"libvips {pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}")
for op in ("jpegload", "pngload", "webpload", "gifload", "heifload", "tiffload", "jxlload"):
    present = pyvips.type_find("VipsOperation", op) != 0
    print(f"  {op:10s} {'yes' if present else 'NO'}")
ffmpeg = shutil.which("ffmpeg")
print(f"ffmpeg {ffmpeg or 'NOT FOUND'}")
if ffmpeg:
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=False)
    print(f"  {version.stdout.splitlines()[0]}")

model_dir = Path(os.environ.get("VISUALSTORE_MODEL_DIR", "/app/models"))
for spec in load_registry(model_dir).values():
    path = resolve_model_path(spec, model_dir)  # verifies the sha256; never downloads here
    vector = Session(path, spec).embed(
        np.zeros((1, 3, spec.input_size, spec.input_size), np.float32)
    )
    assert vector.shape == (1, spec.dim), (spec.id, vector.shape)
    print(f"model {spec.id:24s} {path.stat().st_size / 1e6:6.1f} MB  dim {spec.dim}  ok")

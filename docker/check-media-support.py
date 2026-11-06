"""Print what the image can decode. Runs during the image build so the capability of each
architecture is in the build log (design §14: heif/avif support in arm64 libvips)."""

import shutil
import subprocess

import pyvips

print(f"libvips {pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}")
for op in ("jpegload", "pngload", "webpload", "gifload", "heifload", "tiffload", "jxlload"):
    present = pyvips.type_find("VipsOperation", op) != 0
    print(f"  {op:10s} {'yes' if present else 'NO'}")
ffmpeg = shutil.which("ffmpeg")
print(f"ffmpeg {ffmpeg or 'NOT FOUND'}")
if ffmpeg:
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=False)
    print(f"  {version.stdout.splitlines()[0]}")

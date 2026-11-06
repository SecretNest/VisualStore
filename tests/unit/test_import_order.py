import subprocess
import sys


def test_usearch_module_can_be_followed_by_onnxruntime_in_one_process():
    """Importing onnxruntime after usearch crashes on the runtime image unless usearch_index
    loads onnxruntime first; a fresh interpreter proves the order the package enforces."""
    code = "import visualstore.index.usearch_index, onnxruntime, usearch.index; print('ok')"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stderr[-800:]

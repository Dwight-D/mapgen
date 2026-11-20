"""Run tests/browser/paint_drive.mjs against a spec-edit server on a temp copy of town_square_v4.
Usage: python tests/browser/run_paint_drive.py [screenshot dir]. Needs node (>= 22) and Edge or Chrome; exits 77 when missing."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from run_drive import find_browser  # noqa: E402,F401
from mapgen.spec_editor import SpecEditorServer  # noqa: E402

V4 = os.path.join(ROOT, "maps", "iterations", "town_square", "v4_natural", "town_square_v4.yaml")


def run(shots):
    node, exe = shutil.which("node"), find_browser()
    if not node or not exe:
        return 77, "node or browser not found"
    d = tempfile.mkdtemp()
    spec = os.path.join(d, "town_square_v4.yaml")
    orig = os.path.join(d, "original.txt")
    shutil.copyfile(V4, spec)
    shutil.copyfile(V4, orig)
    srv = SpecEditorServer(spec, preview_px=512)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = subprocess.run([node, os.path.join(HERE, "paint_drive.mjs"), exe, "http://127.0.0.1:%d/" % srv.server_address[1],
                            spec, orig, shots],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        return r.returncode, r.stdout
    finally:
        srv.shutdown()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    code, out = run(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
    print(out)
    sys.exit(code)

"""Run tests/browser/flowgraph_drive.mjs against a spec-edit server on temp copies of town_square_v4 and flowgraphs/town_square.flow.yaml.
Usage: python tests/browser/run_flowgraph_drive.py [screenshot dir]. Needs node (>= 22) and Edge or Chrome; exits 77 when missing."""
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
GRAPH = os.path.join(ROOT, "flowgraphs", "town_square.flow.yaml")


def run(shots):
    node, exe = shutil.which("node"), find_browser()
    if not node or not exe:
        return 77, "node or browser not found"
    d = tempfile.mkdtemp()
    spec = os.path.join(d, "town_square_v4.yaml")
    graph = os.path.join(d, "town_square.flow.yaml")
    shutil.copyfile(V4, spec)
    shutil.copyfile(GRAPH, graph)
    srv = SpecEditorServer(spec, preview_px=512, flowgraph_path=graph)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = subprocess.run([node, os.path.join(HERE, "flowgraph_drive.mjs"), exe, "http://127.0.0.1:%d/" % srv.server_address[1],
                            spec, graph, shots],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        return r.returncode, r.stdout
    finally:
        srv.shutdown()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    code, out = run(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
    print(out)
    sys.exit(code)

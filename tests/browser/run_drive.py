"""Run tests/browser/editor_drive.mjs against a fresh editor server (--new graph in a temp folder).
Usage: python tests/browser/run_drive.py [screenshot dir]. Needs node (>= 22) and Edge or Chrome; exits 77 when missing."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from mapgen.flowgraph_editor import EditorServer  # noqa: E402

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("google-chrome")


def run(shots):
    node, exe = shutil.which("node"), find_browser()
    if not node or not exe:
        return 77, "node or browser not found"
    d = tempfile.mkdtemp()
    graph = os.path.join(d, "sample.flow.yaml")
    srv = EditorServer(graph)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = subprocess.run([node, os.path.join(HERE, "editor_drive.mjs"), exe, "http://127.0.0.1:%d/" % srv.server_address[1], graph, shots],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=240)
        return r.returncode, r.stdout
    finally:
        srv.shutdown()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    code, out = run(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
    print(out)
    sys.exit(code)

"""Entry emission (TOWN_ORGANIC_SPEC 6.5, 9, 10.1) and the open-edge showcase build (maps/towns/build.py)."""
import os
import subprocess
import sys

import pytest
import yaml

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_entries_are_points_without_gate_crossing(seed):
    s = town.generate("organic", seed, size=(1000, 1000), params={"edge": "open"})
    entries = [g for g in s.gates if g.kind == "entry"]
    assert entries and len(entries) == len(s.gates)
    frag = yaml.safe_load(town.emit_spec(s))
    for g in entries:
        assert g.id in frag["points"]
    assert not [c for c in (frag.get("crossings") or []) if c.get("type") == "gate"]
    flow = yaml.safe_load(town.emit_flow(s))
    for g in entries:
        assert flow["nodes"][g.id]["kind"] == "choke"
        assert flow["nodes"][g.id]["name"].startswith("Entry ")


def test_walled_gates_still_cross():
    s = town.generate("organic", 1, size=(1000, 1000), params={"edge": "wall"})
    frag = yaml.safe_load(town.emit_spec(s))
    assert len([c for c in frag["crossings"] if c["type"] == "gate"]) == len(s.gates)


def test_build_thornwick(tmp_path):
    r = subprocess.run([sys.executable, "maps/towns/build.py", "thornwick"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    path = os.path.join(ROOT, "maps", "towns", "thornwick.yaml")
    text = open(path, encoding="utf-8").read()
    spec = schema.load_spec(path)
    assert "type: gate" not in text and "type: wall" not in text
    assert {"A", "B"} <= set(spec.teams)
    for t in "ab":
        line = next(l for l in spec.lines if l.id == "approach_" + t)
        assert line.id and "_entry_" in text.split("id: approach_%s" % t)[1].split("}")[0]
    flow = yaml.safe_load(open(os.path.join(ROOT, "maps", "towns", "thornwick.flow.yaml"), encoding="utf-8"))
    g = load_flowgraph_dict(flow)
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


def test_build_writes_settlement_sidecar():
    r = subprocess.run([sys.executable, "maps/towns/build.py", "thornwick"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sidecar = os.path.join(ROOT, "maps", "towns", "thornwick.settlement.json")
    with open(sidecar, encoding="utf-8") as f:
        text = f.read()
    sys.path.insert(0, os.path.join(ROOT, "maps", "towns"))
    import build
    c = build.MAPS["thornwick"]
    s = town.generate(c["gen"], c["seed"], (c["size"], c["size"]), {k: str(v) for k, v in c["params"].items()}, c["sid"])
    assert text == s.to_json()

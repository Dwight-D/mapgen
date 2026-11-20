"""Settlement core model: pure data, JSON-serialisable (design: mapgen/TOWN_SPEC.md section 3)."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Tuple

FORMAT_VERSION = 1
Point = List[float]


def r2(v: float) -> float:
    """Round to 0.01 m; never -0.0."""
    return round(float(v), 2) + 0.0


def pt(x: float, y: float) -> Point:
    return [r2(x), r2(y)]


@dataclass
class District:
    id: str
    key: str
    kind: str
    polygon: List[Point]            # exterior ring, counter-clockwise, first point not repeated
    area: float
    label: str
    inside_wall: bool = True
    waterfront: bool = False
    landmarks: List[str] = field(default_factory=list)


@dataclass
class Road:
    id: str
    cls: str                        # artery | ring | boulevard | bridge | approach
    polyline: List[Point]           # a closed ring repeats its first point
    width: float
    nodes: List[str] = field(default_factory=list)


@dataclass
class Wall:
    id: str
    kind: str                       # outer | inner | citadel
    polyline: List[Point]           # closed: first point repeated
    thickness: float
    open_shore: List[List[int]] = field(default_factory=list)   # [first, last] vertex index ranges without wall


@dataclass
class Gate:
    id: str
    wall: str
    at: Point
    normal: Point                   # unit vector pointing out of the walled area
    road: str
    kind: str = "gate"              # gate | postern | water_gate


@dataclass
class Landmark:
    id: str
    kind: str                       # square | market | citadel | fortress | temple | docks
    at: Point
    r: float
    district: str
    label: str = ""


@dataclass
class GraphNode:
    id: str
    kind: str                       # zone (square, citadel) | choke (gate, junction)
    at: Point
    districts: List[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    id: str
    a: str
    b: str
    cls: str                        # artery | ring | boulevard | bridge
    road: str
    length: float
    passable: bool = True


@dataclass
class DistrictNode:
    id: str
    kind: str                       # district | outside


@dataclass
class DistrictEdge:
    id: str
    a: str
    b: str
    kind: str                       # adjacent | wall
    length: float
    passable: bool
    gates: List[str] = field(default_factory=list)
    roads: List[str] = field(default_factory=list)


@dataclass
class Settlement:
    id: str
    generator: str
    seed: int
    params: Dict[str, Any]
    size: Tuple[float, float]
    centre: Point
    footprint: List[Point]
    districts: List[District] = field(default_factory=list)
    roads: List[Road] = field(default_factory=list)
    walls: List[Wall] = field(default_factory=list)
    gates: List[Gate] = field(default_factory=list)
    landmarks: List[Landmark] = field(default_factory=list)
    water: Dict[str, Any] = field(default_factory=dict)
    graph_nodes: List[GraphNode] = field(default_factory=list)
    graph_edges: List[GraphEdge] = field(default_factory=list)
    district_nodes: List[DistrictNode] = field(default_factory=list)
    district_edges: List[DistrictEdge] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        def lst(items):
            out = []
            for it in items:
                d = asdict(it)
                if "cls" in d:
                    d["class"] = d.pop("cls")
                out.append(d)
            return out
        return {
            "format_version": FORMAT_VERSION,
            "id": self.id,
            "generator": self.generator,
            "seed": self.seed,
            "params": self.params,
            "size": [self.size[0], self.size[1]],
            "centre": self.centre,
            "footprint": self.footprint,
            "districts": lst(self.districts),
            "roads": lst(self.roads),
            "walls": lst(self.walls),
            "gates": lst(self.gates),
            "landmarks": lst(self.landmarks),
            "water": self.water,
            "graph": {"nodes": lst(self.graph_nodes), "edges": lst(self.graph_edges)},
            "district_graph": {"nodes": lst(self.district_nodes), "edges": lst(self.district_edges)},
        }

    def to_json(self) -> str:
        """Canonical text: sorted keys, 2-space indent, LF, trailing newline. Same Settlement => same bytes."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Settlement":
        if d.get("format_version") != FORMAT_VERSION:
            raise ValueError("unsupported settlement format_version %r (expected %d)" % (d.get("format_version"), FORMAT_VERSION))

        def mk(cls, items):
            out = []
            for it in items:
                it = dict(it)
                if "class" in it:
                    it["cls"] = it.pop("class")
                out.append(cls(**it))
            return out
        g, dg = d["graph"], d["district_graph"]
        return Settlement(
            id=d["id"], generator=d["generator"], seed=d["seed"], params=d["params"],
            size=(d["size"][0], d["size"][1]), centre=d["centre"], footprint=d["footprint"],
            districts=mk(District, d["districts"]), roads=mk(Road, d["roads"]), walls=mk(Wall, d["walls"]),
            gates=mk(Gate, d["gates"]), landmarks=mk(Landmark, d["landmarks"]), water=d.get("water", {}),
            graph_nodes=mk(GraphNode, g["nodes"]), graph_edges=mk(GraphEdge, g["edges"]),
            district_nodes=mk(DistrictNode, dg["nodes"]), district_edges=mk(DistrictEdge, dg["edges"]))

    @staticmethod
    def from_json(text: str) -> "Settlement":
        return Settlement.from_dict(json.loads(text))


def polyline_length(pts: List[Point]) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))

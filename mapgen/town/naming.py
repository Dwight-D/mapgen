"""District naming (design: mapgen/TOWN_SPEC.md section 9.1).

Only top-level (depth-0) districts are named, 3-5 per settlement; every deeper district is unnamed. A top-level district is
the depth-0 ancestor of each emitted district (the district itself when it is not subdivided). Named districts get a kind-based
name with seeded variety; the name is carried by the largest emitted district of its group, so each name appears once.
"""
from __future__ import annotations

import random
from typing import Dict, List

from ..schema import feature_seed
from .model import Settlement

MAX_NAMED = 5
KIND_WEIGHT = {"citadel": 3.0, "harbour": 2.5, "core": 2.0, "market": 2.0, "temple": 1.5, "craft": 1.2,
               "residential": 1.0, "slum": 0.8}       # outskirts are never named
NAME_POOL = {
    "core": ["Central Plaza"],
    "citadel": ["Citadel", "The Keep", "Castle Hill"],
    "harbour": ["Harbourside", "The Docks", "Quayside", "Wharf District"],
    "market": ["Market Quarter", "Merchants' Row", "Mercantile Quarter", "Bazaar Quarter", "Cornmarket", "Guildhall Quarter",
               "Traders' Reach", "High Market"],
    "temple": ["Temple Quarter", "Cathedral Close", "Pilgrims' Rest"],
    "craft": ["Smiths' Quarter", "Artisans' Row", "Foundry Quarter", "Weavers' Quarter", "Potters' Row", "Tanners' Quarter"],
    "residential": ["Old Town", "Hearthside", "Lowtown", "Tanners' Hill", "Westgate", "Chapel Hill", "Greenhithe", "Kettle Row", "Millside"],
    "slum": ["The Warrens", "The Rookery", "Ashlane"],
}


def top_level_of(s: Settlement) -> Dict[str, str]:
    """District id -> id of its depth-0 ancestor (itself at depth 0)."""
    parent = {n.id: n.parent for n in s.layers}
    top = {}
    for d in s.districts:
        cur = d.id if d.depth == 0 else d.parent
        while cur in parent and parent[cur]:
            cur = parent[cur]
        top[d.id] = cur
    return top


def district_names(s: Settlement) -> Dict[str, str]:
    """{district id: name} for the emitted districts that carry a name: one per named top-level group (its largest district).

    Rule: groups are ranked by area x KIND_WEIGHT[dominant kind by area] (ties by id); the first MAX_NAMED non-outskirt
    groups are named, the rest stay unnamed. Names come from NAME_POOL[kind], drawn in a seeded shuffle without repeats."""
    top = top_level_of(s)
    groups: Dict[str, List] = {}
    for d in s.districts:
        groups.setdefault(top[d.id], []).append(d)
    ranked = []
    for gid, ds in groups.items():
        by_kind: Dict[str, float] = {}
        for d in ds:
            by_kind[d.kind] = by_kind.get(d.kind, 0.0) + d.area
        kind = max(sorted(by_kind), key=lambda k: by_kind[k])
        if kind not in KIND_WEIGHT:
            continue
        ranked.append((-sum(d.area for d in ds) * KIND_WEIGHT[kind], gid, kind, ds))
    ranked.sort(key=lambda t: (t[0], t[1]))
    rng = random.Random(feature_seed("%s_district_names" % s.id))
    pools = {k: rng.sample(v, len(v)) for k, v in sorted(NAME_POOL.items())}
    used: Dict[str, int] = {}
    out: Dict[str, str] = {}
    for _, gid, kind, ds in ranked[:MAX_NAMED]:
        pool = pools[kind]
        n = used.get(kind, 0)
        used[kind] = n + 1
        name = pool[n] if n < len(pool) else "%s %d" % (pool[n % len(pool)], n // len(pool) + 1)
        carrier = max(ds, key=lambda d: (d.area, d.id))
        out[carrier.id] = name
    return out

"""District naming: only 3-5 top-level districts are named (design: mapgen/TOWN_SPEC.md section 9.1)."""
import re

from mapgen import town
from mapgen.town.naming import district_names, top_level_of
from mapgen.town.emit_spec import emit_spec


def _gen():
    return town.generate("organic", 4, size=(1000, 1000), params={"layers": "organic-radial", "radius_frac": "0.55", "edge": "wall", "water": "river"})


def test_named_count_and_top_level():
    s = _gen()
    names = district_names(s)
    assert 3 <= len(names) <= 5
    top = top_level_of(s)
    assert len({top[i] for i in names}) == len(names)
    assert len(set(names.values())) == len(names)
    assert not any(re.search(r"Ward|\d", n) for n in names.values())


def test_emit_labels_only_named_and_deterministic():
    s = _gen()
    text = emit_spec(s)
    assert text == emit_spec(_gen())
    region = [l for l in text.splitlines() if "type: town" in l]
    assert len(region) == len(s.districts)
    assert sum("label:" in l for l in region) == len(district_names(s))

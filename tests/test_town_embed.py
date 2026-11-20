from mapgen.town.embed import merge

BASE = "name: x\npoints:\n  a: [1, 2]\n# note\nregions:\n  - { id: r }\nteams:\n  A: {}\n"
FRAG = "# hdr\npoints:\n  t_b: [3, 4]\nlines:\n  - { id: l }\nregions:\n  - { id: t_d }\n"


def test_merge_appends_into_sections_and_adds_missing():
    out = merge(BASE, [FRAG])
    lines = out.split("\n")
    assert lines.index("  t_b: [3, 4]") == lines.index("  a: [1, 2]") + 2
    assert lines.index("  - { id: t_d }") == lines.index("  - { id: r }") + 1
    assert "lines:" in lines and "  - { id: l }" in lines
    assert "# note" in lines and out.endswith("\n")
    assert merge(BASE, [FRAG]) == out

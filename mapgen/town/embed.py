"""Merge settlement fragments into a map spec or flowgraph text (TOWN_SPEC section 9, form A, automated).

    python -m mapgen.town.embed BASE.yaml FRAGMENT.yaml [FRAGMENT.yaml ...] --out OUT.yaml [--force]

Text-level and deterministic: for every top-level key of a fragment (`points`, `regions`, `lines`, `crossings`, `pois`
of a spec fragment; `nodes`, `edges` of a flow fragment) the fragment's body lines are appended to the end of the same
key's section in BASE, or a new section is appended when BASE has none. Comments and layout of BASE are kept.
No YAML is parsed, so ids are not checked: run `mapgen validate` on the result.
"""
import argparse
import os
import sys
from typing import Dict, List, Tuple


def _split(text: str) -> Tuple[List[str], List[Tuple[str, List[str]]]]:
    """(preamble lines, [(key, lines of the section including its key line)]); a section starts at a column-0 `key:` line."""
    pre = []  # type: List[str]
    secs = []  # type: List[Tuple[str, List[str]]]
    for line in text.replace("\r\n", "\n").split("\n"):
        head = line.split(":", 1)[0]
        if line and line[0] not in " \t#-" and ":" in line and head.replace("_", "").isalnum():
            secs.append((head, [line]))
        elif secs:
            secs[-1][1].append(line)
        else:
            pre.append(line)
    return pre, secs


def _body(lines: List[str]) -> List[str]:
    """Section lines after the key line, minus comments and blanks."""
    return [l for l in lines[1:] if l.strip() and not l.lstrip().startswith("#")]


def merge(base: str, fragments: List[str]) -> str:
    pre, secs = _split(base)
    index = {k: i for i, (k, _) in enumerate(secs)}  # type: Dict[str, int]
    for frag in fragments:
        _, fsecs = _split(frag)
        for key, lines in fsecs:
            body = _body(lines)
            if key in index:
                sec = secs[index[key]][1]
                while sec and not sec[-1].strip():
                    sec.pop()
                sec.extend(body)
                sec.append("")
            else:
                index[key] = len(secs)
                secs.append((key, [key + ":"] + body + [""]))
    out = list(pre)
    for _, lines in secs:
        out.extend(lines)
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mapgen.town.embed", description=__doc__.split("\n")[0])
    ap.add_argument("base")
    ap.add_argument("fragment", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    a = ap.parse_args(argv)
    if os.path.exists(a.out) and not a.force:
        print("error: %s exists (use --force)" % a.out)
        return 1
    texts = []
    for p in [a.base] + a.fragment:
        try:
            with open(p, "r", encoding="utf-8") as f:
                texts.append(f.read())
        except OSError as e:
            print("error: %s" % e)
            return 1
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(merge(texts[0], texts[1:]))
    print("OK: %s <- %s + %d fragment(s)" % (a.out, a.base, len(a.fragment)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

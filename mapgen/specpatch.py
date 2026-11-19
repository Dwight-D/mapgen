"""Span-patch engine: edit values of a map spec as exact text spans, leaving every other byte untouched.

The spec text is composed with the loader's YAML semantics (`schema._SpecLoader`) to get node marks; an edit is
a list of (start, end, replacement) spans applied to the original string. Comments, quoting, indentation, key order
and line endings outside the spans are preserved by construction. Every patch is verified: the patched text is
re-parsed and must equal the old document with exactly the requested change applied; `patch_text` then runs the
real loader (`schema.load_spec_text`) on the result. Contract: MAPGEN_SPEC.md "spec set", README.md.

Path grammar: `seg(.seg | [n])*`. `seg` is `[A-Za-z_][A-Za-z0-9_-]*`: a mapping key, or, when the current node is a
sequence of mappings, the `id` of one item (`pois.fa.at`, `lines.river_main.path[2]`). `[n]` is a zero-based position
in a sequence that has no ids (a waypoint, a vertex). `[+n]` / `[+]` (last token only) is an insertion point.

Structure ops (slice 3): `Put` creates a missing key (or sets an existing one), `Delete` also removes a key or a whole
feature (refused while other features reference it, unless `detach_refs`), `Add` appends a feature with a generated unique
id (creating a missing top-level section), `Rename` rewrites an id and every reference to it.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from collections import OrderedDict
from typing import Any, List, Optional, Tuple

import yaml

from . import schema


class PatchError(Exception):
    """Base class of every refusal of the patch engine."""


class PathError(PatchError):
    """The path is malformed or addresses nothing."""


class UnsafePatch(PatchError):
    """The target cannot be patched safely (anchor, alias, merge key, duplicate key, block scalar, comment in the way...)."""


class VerifyError(PatchError):
    """The patched text does not differ from the original by exactly the requested change."""


# ----------------------------------------------------------------------------- operations

class Set:
    """Assign `value` (a scalar, or a list such as [x, y]) to the node at `path`. A point-reference string
    replaced by a coordinate pair moves the referenced definition (POI `at`, `points` entry, team `base`) unless
    `detach` is true, which writes the literal pair at the vertex."""

    def __init__(self, path: str, value: Any, detach: bool = False):
        self.path, self.value, self.detach = path, value, detach


class Insert:
    """Insert `value` into the sequence at `path` before position `index` (`None` or the length: append)."""

    def __init__(self, path: str, index: Optional[int], value: Any):
        self.path, self.index, self.value = path, index, value


class Delete:
    """Delete the sequence item at `path` (last token `[n]`), the mapping key at `path` (last token a key that is not a
    feature), or the feature at `path` (`pois.fa`, `lines.river_2`, `points.ao`). A feature that other features reference
    is refused (`ReferencedError`) unless `detach_refs`: point references become the literal coordinates, a `join` is removed."""

    def __init__(self, path: str, detach_refs: bool = False):
        self.path, self.detach_refs = path, detach_refs
        self.result = {}  # type: dict


class Put:
    """Set the key at `path` to `value`, creating the key when the mapping lacks it (appended, or placed after `after`)."""

    def __init__(self, path: str, value: Any, after: Optional[str] = None):
        self.path, self.value, self.after = path, value, after


class Add:
    """Append a feature to `section` (regions | lines | crossings | pois | scatter | points). The id is `id`, else the value's
    own `id`, else generated (`<type>_<n>`, `poi_<n>`, points `p<n>`) and never one of `avoid`. `result["id"]` is set on apply."""

    def __init__(self, section: str, value: Any, id: Optional[str] = None, avoid=()):
        self.section, self.value, self.id, self.avoid = section, value, id, set(avoid)
        self.result = {}  # type: dict


class Rename:
    """Rename a feature id (or `points` name) and every reference to it. A region, line or scatter entry without an explicit
    `seed` gets `seed: <old id-derived seed>` so its shape does not change."""

    def __init__(self, old: str, new: str, avoid=()):
        self.old, self.new, self.avoid = old, new, set(avoid)
        self.result = {}  # type: dict


class ReferencedError(PatchError):
    """A feature that other features reference cannot be deleted; `referrers` lists them."""

    def __init__(self, name: str, referrers: List[dict]):
        super().__init__("%s is referenced by %s" % (name, ", ".join(r["path"] for r in referrers)))
        self.name, self.referrers = name, referrers


DELETE_TOKEN = "@delete"
SEQ_SECTIONS = ("regions", "lines", "crossings", "pois", "scatter")   # sequences of mappings addressed by `id`
ADD_SECTIONS = SEQ_SECTIONS + ("points",)
_TOKEN = re.compile(r"(?:\.?([A-Za-z_][A-Za-z0-9_-]*))|(?:\[(\+?)(\d*)\])")


def _tokens(path: str) -> List[Tuple[str, Any]]:
    out = []  # type: List[Tuple[str, Any]]
    pos = 0
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m or m.end() == pos or (pos == 0 and path[0] == "."):
            raise PathError("bad path %r at offset %d" % (path, pos))
        if m.group(1) is not None:
            out.append(("key", m.group(1)))
        elif m.group(2):
            out.append(("ins", int(m.group(3)) if m.group(3) else None))
        elif m.group(3):
            out.append(("idx", int(m.group(3))))
        else:
            raise PathError("bad path %r at offset %d" % (path, pos))
        pos = m.end()
    if not out:
        raise PathError("empty path")
    if any(k == "ins" for k, _ in out[:-1]):
        raise PathError("an insertion point [+n] must be the last path token (%r)" % path)
    return out


def parse_value(raw: str) -> Any:
    """JSON first, then YAML flow syntax (`[base_A, [1, 2]]`, bare words such as `forest`)."""
    if not raw.strip():
        raise PatchError("empty value")

    def _no_const(c):
        raise ValueError(c)
    try:
        v = json.loads(raw, parse_constant=_no_const)
    except ValueError:
        try:
            v = yaml.load(raw, Loader=schema._SpecLoader)
        except yaml.YAMLError as e:
            raise PatchError("cannot parse value %r: %s" % (raw, str(e).replace("\n", " ")))
    _check_value(v)
    return v


def parse_op(arg: str, detach: bool = False, create: bool = False, detach_refs: bool = False):
    """`path=value` -> Set (Put with `create`: a missing key is added); `path[+n]=value` -> Insert;
    `path[n]=@delete`, `path.key=@delete` (a key or a whole feature) -> Delete."""
    if "=" not in arg:
        raise PatchError("expected <path>=<value>, got %r" % arg)
    path, raw = arg.split("=", 1)
    toks = _tokens(path)
    if raw.strip() == DELETE_TOKEN:
        if toks[-1][0] not in ("idx", "key"):
            raise PathError("%s needs a key or a sequence position as the last token: %r" % (DELETE_TOKEN, path))
        return Delete(path, detach_refs)
    value = parse_value(raw)
    if toks[-1][0] == "ins":
        return Insert(path, toks[-1][1], value)
    if create:
        return Put(path, value)
    return Set(path, value, detach)


def op_from_json(d: Any):
    """Wire form of an op -> Set / Insert / Delete (the editors' API). Shapes:
    {"op": "set", "path": "pois.fa.at", "value": [x, y], "detach": false}
    {"op": "insert", "path": "lines.river.path", "index": 2, "value": [x, y]}   (index null: append)
    {"op": "delete", "path": "lines.river.path[2]"}          (also a key, or a feature: "lines.river_2", "detach_refs": true)
    {"op": "put", "path": "regions.forest_3.rough", "value": 0.4, "after": null}   (creates the key when missing)
    {"op": "add", "section": "regions", "value": {...}, "id": null}                 (id generated when absent)
    {"op": "rename", "from": "forest_3", "to": "north_wood"}"""
    if isinstance(d, dict) and d.get("op") == "add":
        sec = d.get("section")
        if sec not in ADD_SECTIONS:
            raise PatchError("add needs a section (%s)" % ", ".join(ADD_SECTIONS))
        if "value" not in d:
            raise PatchError("add needs a value")
        _check_value(d["value"])
        if d.get("id") is not None and not isinstance(d["id"], str):
            raise PatchError("add id must be a string")
        return Add(sec, d["value"], d.get("id"))
    if isinstance(d, dict) and d.get("op") == "rename":
        if not isinstance(d.get("from"), str) or not isinstance(d.get("to"), str):
            raise PatchError("rename needs string from and to")
        return Rename(d["from"], d["to"])
    if not isinstance(d, dict) or not isinstance(d.get("path"), str):
        raise PatchError("an op is an object with a string path")
    kind, path = d.get("op"), d["path"]
    if kind == "set":
        _tokens(path)
        if "value" not in d:
            raise PatchError("set needs a value")
        _check_value(d["value"])
        return Set(path, d["value"], bool(d.get("detach", False)))
    if kind == "insert":
        index = d.get("index")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
            raise PatchError("insert index must be an integer >= 0 or null")
        if "value" not in d:
            raise PatchError("insert needs a value")
        _check_value(d["value"])
        full = "%s[+%s]" % (path, "" if index is None else index)
        _tokens(full)
        return Insert(full, index, d["value"])
    if kind == "delete":
        if _tokens(path)[-1][0] not in ("idx", "key"):
            raise PathError("delete needs a key, feature id or sequence position as the last token: %r" % path)
        return Delete(path, bool(d.get("detach_refs", False)))
    if kind == "put":
        if _tokens(path)[-1][0] != "key":
            raise PathError("put needs a key as the last token: %r" % path)
        if "value" not in d:
            raise PatchError("put needs a value")
        _check_value(d["value"])
        after = d.get("after")
        if after is not None and not isinstance(after, str):
            raise PatchError("put after must be a key name")
        return Put(path, d["value"], after)
    raise PatchError("unknown op %r (set | put | insert | delete | add | rename)" % (kind,))


def _check_value(v: Any) -> None:
    if v is None or isinstance(v, (bool, str)):
        return
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise PatchError("non-finite number in value")
        return
    if isinstance(v, list):
        for x in v:
            _check_value(x)
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise PatchError("mapping keys of a value must be strings")
            _check_value(x)
        return
    raise PatchError("unsupported value %r" % (v,))


# ----------------------------------------------------------------------------- composing

class _MarkLoader(schema._SpecLoader):
    """Composes with the spec loader's resolvers and records every node that carries an anchor or is an alias."""

    def __init__(self, stream):
        super().__init__(stream)
        self.flagged = set()  # type: set

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            flagged = True
        else:
            flagged = self.peek_event().anchor is not None
        node = super().compose_node(parent, index)
        if flagged:
            self.flagged.add(id(node))
        return node


def _scan_hazards(root) -> dict:
    """id(mapping node) -> ("merge",) | ("dup", key) for mappings with a merge key or a repeated key."""
    out = {}
    seen = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if isinstance(n, yaml.MappingNode):
            keys = set()
            for kn, vn in n.value:
                if kn.tag == "tag:yaml.org,2002:merge":
                    out[id(n)] = ("merge",)
                elif isinstance(kn, yaml.ScalarNode):
                    if kn.value in keys and id(n) not in out:
                        out[id(n)] = ("dup", kn.value)
                    keys.add(kn.value)
                stack.append(kn)
                stack.append(vn)
        elif isinstance(n, yaml.SequenceNode):
            stack.extend(n.value)
    return out


class _Doc:
    """The composed node graph of a spec text (marks, anchor flags) and its constructed Python value, from one parse."""

    def __init__(self, text: str):
        self.text = text
        loader = _MarkLoader(text)
        try:
            self.root = loader.get_single_node()
            if self.root is None:
                raise PatchError("empty document")
            self.hazards = _scan_hazards(self.root)   # before construction: it flattens merge keys out of the nodes
            try:
                self.py = loader.construct_document(self.root)
            except yaml.YAMLError as e:
                raise UnsafePatch("the spec does not parse (%s)" % str(e).replace("\n", " "))
        except yaml.YAMLError as e:
            raise PatchError("cannot parse the spec: %s" % str(e).replace("\n", " "))
        finally:
            self.flagged = loader.flagged
            loader.dispose()


_DOC_CACHE = OrderedDict()  # text -> _Doc; the result of one op is the input of the next (a drag, a nudge series)
_DOC_LOCK = threading.Lock()
_DOC_CACHE_SIZE = 4


def _doc(text: str) -> "_Doc":
    """The parsed `_Doc` of `text`, from a small cache keyed by the text. Callers never mutate `.py`."""
    with _DOC_LOCK:
        hit = _DOC_CACHE.get(text)
        if hit is not None:
            _DOC_CACHE.move_to_end(text)
            return hit
    doc = _Doc(text)
    with _DOC_LOCK:
        _DOC_CACHE[text] = doc
        while len(_DOC_CACHE) > _DOC_CACHE_SIZE:
            _DOC_CACHE.popitem(last=False)
    return doc


def parsed_document(text: str) -> Any:
    """The spec text parsed with the loader's YAML semantics (shared, read-only; cached)."""
    return _doc(text).py


def _same(a: Any, b: Any) -> bool:
    """Structural equality; ints and floats compare by value, bool is its own type."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a.keys()) == list(b.keys()) and all(_same(a[k], b[k]) for k in a)
    return type(a) == type(b) and a == b


def _diff_path(a: Any, b: Any, path: str = "") -> str:
    if isinstance(a, dict) and isinstance(b, dict) and list(a.keys()) == list(b.keys()):
        for k in a:
            if not _same(a[k], b[k]):
                return _diff_path(a[k], b[k], "%s.%s" % (path, k))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if not _same(x, y):
                return _diff_path(x, y, "%s[%d]" % (path, i))
    return path or "<root>"


def _guard(doc: _Doc, node, what: str) -> None:
    if id(node) in doc.flagged:
        raise UnsafePatch("%s carries a YAML anchor or alias; edit it by hand" % what)
    hz = doc.hazards.get(id(node))
    if hz is not None:
        if hz[0] == "merge":
            raise UnsafePatch("%s is in a mapping with a merge key (<<); edit it by hand" % what)
        raise UnsafePatch("%s is in a mapping with the duplicate key %r" % (what, hz[1]))


def _guard_subtree(doc: _Doc, node, what: str) -> None:
    stack = [node]
    while stack:
        n = stack.pop()
        _guard(doc, n, what)
        if isinstance(n, yaml.MappingNode):
            for kn, vn in n.value:
                stack.append(kn)
                stack.append(vn)
        elif isinstance(n, yaml.SequenceNode):
            stack.extend(n.value)


def _item_id(item) -> Optional[str]:
    if isinstance(item, yaml.MappingNode):
        for kn, vn in item.value:
            if isinstance(kn, yaml.ScalarNode) and kn.value == "id" and isinstance(vn, yaml.ScalarNode):
                return vn.value
    return None


def _resolve(doc: _Doc, tokens, what: str):
    """Walk the node tree. Returns (node, loc, parent_seq) where `loc` is the concrete location (mapping keys and
    sequence positions) in the parsed document, and `parent_seq` is the sequence node of the last token when it
    was `idx` or `ins`."""
    node = doc.root
    loc = []  # type: List[Any]
    parent = None
    for ti, (kind, arg) in enumerate(tokens):
        _guard(doc, node, "%s (at %s)" % (what, "/".join(str(x) for x in loc) or "the document root"))
        parent = None
        if kind == "key":
            if isinstance(node, yaml.MappingNode):
                for kn, vn in node.value:
                    if isinstance(kn, yaml.ScalarNode) and kn.value == arg:
                        node = vn
                        loc.append(arg)
                        break
                else:
                    raise PathError("%s: no key %r" % (what, arg))
            elif isinstance(node, yaml.SequenceNode):
                hits = [i for i, it in enumerate(node.value) if _item_id(it) == arg]
                if not hits:
                    raise PathError("%s: no item with id %r" % (what, arg))
                if len(hits) > 1:
                    raise UnsafePatch("%s: id %r occurs %d times" % (what, arg, len(hits)))
                loc.append(hits[0])
                node = node.value[hits[0]]
            else:
                raise PathError("%s: cannot take %r of a scalar" % (what, arg))
        elif kind == "idx":
            if not isinstance(node, yaml.SequenceNode):
                raise PathError("%s: [%d] needs a sequence" % (what, arg))
            if any(_item_id(it) is not None for it in node.value):
                raise PathError("%s: items of this sequence have ids; address them by id, not position" % what)
            if arg >= len(node.value):
                raise PathError("%s: index %d out of range (%d items)" % (what, arg, len(node.value)))
            parent = node
            loc.append(arg)
            node = node.value[arg]
        else:  # ins
            if not isinstance(node, yaml.SequenceNode):
                raise PathError("%s: [+n] needs a sequence" % what)
            if arg is not None and arg > len(node.value):
                raise PathError("%s: insert index %d out of range (%d items)" % (what, arg, len(node.value)))
            parent = node
    return node, loc, parent


def _py_get(root, loc):
    v = root
    for k in loc:
        v = v[k]
    return v


# ----------------------------------------------------------------------------- rendering

_NULLISH = {"null", "Null", "NULL", "true", "True", "TRUE", "false", "False", "FALSE", "~"}
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def fmt_num(v) -> str:
    """1 m integers when the value is whole, else the shortest decimal."""
    if isinstance(v, float):
        if not math.isfinite(v):
            raise PatchError("non-finite number")
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        s = repr(v)
        if "e" in s or "E" in s:
            s = ("%.6f" % v).rstrip("0").rstrip(".")
        return s
    return str(v)


def _fmt_str(s: str, style: Optional[str] = None) -> str:
    if style == "'" and "\n" not in s and all(ord(c) >= 32 for c in s):
        return "'" + s.replace("'", "''") + "'"
    if style is None and _PLAIN.match(s) and s not in _NULLISH:
        return s
    return json.dumps(s, ensure_ascii=False)


def render(v: Any, style: Optional[str] = None) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return fmt_num(v)
    if isinstance(v, str):
        return _fmt_str(v, style)
    if isinstance(v, list):
        return "[" + ", ".join(render(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join("%s: %s" % (k, render(x)) for k, x in v.items()) + " }" if v else "{}"
    raise PatchError("unsupported value %r" % (v,))


def _eol(text: str) -> str:
    crlf = text.count("\r\n")
    return "\r\n" if crlf and crlf * 2 >= text.count("\n") else "\n"


# ----------------------------------------------------------------------------- span computation

def _assign(doc: _Doc, node, old: Any, value: Any, spans: list, what: str) -> None:
    text = doc.text
    if isinstance(node, yaml.ScalarNode):
        if node.style in ("|", ">"):
            raise UnsafePatch("%s is a block scalar" % what)
        s, e = node.start_mark.index, node.end_mark.index
        if "\n" in text[s:e]:
            raise UnsafePatch("%s is a multi-line scalar" % what)
        if _same(old, value):
            return
        spans.append((s, e, render(value, node.style if isinstance(value, str) else None)))
    elif isinstance(node, yaml.SequenceNode):
        if isinstance(value, list) and len(value) == len(node.value) == len(old):
            for i, (child, ov, nv) in enumerate(zip(node.value, old, value)):
                _assign(doc, child, ov, nv, spans, "%s[%d]" % (what, i))
        elif node.flow_style:
            s, e = node.start_mark.index, node.end_mark.index
            if "#" in text[s:e]:
                raise UnsafePatch("%s contains a comment; edit it by hand" % what)
            spans.append((s, e, render(value)))
        else:
            raise UnsafePatch("%s is a block sequence whose shape would change; edit it by hand" % what)
    else:
        raise UnsafePatch("%s is a mapping; set one of its keys instead" % what)


def _gap_ok(text: str, a, b) -> Optional[str]:
    gap = text[a.end_mark.index:b.start_mark.index]
    return gap if re.fullmatch(r"[ \t]*,[ \t\r\n]*", gap) else None


def _last_end(node) -> int:
    if isinstance(node, yaml.ScalarNode) or node.flow_style:
        return node.end_mark.index
    best = node.start_mark.index
    kids = node.value if isinstance(node, yaml.SequenceNode) else [x for kv in node.value for x in kv]
    for k in kids:
        best = max(best, _last_end(k))
    return best


def _block_item_lines(doc: _Doc, item) -> Tuple[int, int, str]:
    """(line start of the `- ` line, end of the item's last line incl. its terminator, indent)."""
    text = doc.text
    start = item.start_mark.index
    ls = text.rfind("\n", 0, start) + 1
    m = re.fullmatch(r"([ \t]*)-[ \t]+", text[ls:start])
    if not m:
        raise UnsafePatch("block sequence item is not on its own `- ` line; edit it by hand")
    end = _last_end(item)
    nl = text.find("\n", end)
    stop = len(text) if nl < 0 else nl + 1
    rest = text[end:stop].strip()
    if rest and not rest.startswith("#"):
        raise UnsafePatch("unexpected text after a block sequence item; edit it by hand")
    return ls, stop, m.group(1)


def _guard_items(doc: _Doc, seq, what: str) -> None:
    """Marks of an aliased item point at its anchor, so a sequence holding one cannot be spliced."""
    for it in seq.value:
        if id(it) in doc.flagged:
            raise UnsafePatch("%s: the sequence holds an anchored or aliased item; edit it by hand" % what)


def _insert_span(doc: _Doc, seq, index: Optional[int], value: Any, spans: list, what: str, block_fmt=None) -> None:
    """`block_fmt(indent, eol)` renders the item's lines (no final newline) of a block sequence; default one `- <flow>` line."""
    text = doc.text
    _guard_items(doc, seq, what)
    items = seq.value
    n = len(items)
    idx = n if index is None else index
    if seq.flow_style:
        r = render(value)
        if n == 0:
            spans.append((seq.start_mark.index + 1,) * 2 + (r,))
            return
        if idx == 0:
            sep = (_gap_ok(text, items[0], items[1]) if n > 1 else None) or ", "
            spans.append((items[0].start_mark.index,) * 2 + (r + sep,))
        else:
            if idx < n:
                sep = _gap_ok(text, items[idx - 1], items[idx])
            elif n >= 2:
                sep = _gap_ok(text, items[n - 2], items[n - 1])
            else:
                sep = None
            spans.append((items[idx - 1].end_mark.index,) * 2 + ((sep or ", ") + r,))
    else:
        if n == 0:
            raise UnsafePatch("%s is an empty block sequence" % what)
        eol = _eol(text)
        fmt = block_fmt or (lambda ind, eo: "%s- %s" % (ind, render(value)))
        if idx == 0:
            ls, _, indent = _block_item_lines(doc, items[0])
            spans.append((ls, ls, fmt(indent, eol) + eol))
        else:
            _, stop, indent = _block_item_lines(doc, items[idx - 1])
            line = fmt(indent, eol)
            if stop == len(text) and not text.endswith("\n"):
                spans.append((stop, stop, eol + line))
            else:
                spans.append((stop, stop, line + eol))


def _delete_span(doc: _Doc, seq, index: int, spans: list, what: str) -> None:
    text = doc.text
    _guard_items(doc, seq, what)
    items = seq.value
    n = len(items)
    _guard_subtree(doc, items[index], what)
    if seq.flow_style:
        it = items[index]
        if n == 1:
            end = it.end_mark.index
            j = end
            while j < len(text) and text[j] in " \t":
                j += 1
            if j < len(text) and text[j] == ",":
                end = j + 1
            spans.append((it.start_mark.index, end, ""))
        elif index < n - 1:
            if _gap_ok(text, it, items[index + 1]) is None:
                raise UnsafePatch("%s: comment or odd separator next to the item; edit it by hand" % what)
            spans.append((it.start_mark.index, items[index + 1].start_mark.index, ""))
        else:
            if _gap_ok(text, items[index - 1], it) is None:
                raise UnsafePatch("%s: comment or odd separator next to the item; edit it by hand" % what)
            spans.append((items[index - 1].end_mark.index, it.end_mark.index, ""))
    else:
        if n == 1:
            raise UnsafePatch("%s: deleting the only item of a block sequence; edit it by hand" % what)
        ls, stop, _ = _block_item_lines(doc, items[index])
        spans.append((ls, stop, ""))


def _apply_spans(text: str, spans: list) -> str:
    spans = sorted(spans, key=lambda s: (s[0], s[1]))
    for a, b in zip(spans, spans[1:]):
        if b[0] < a[1]:
            raise PatchError("internal: overlapping spans")
    for s, e, r in reversed(spans):
        text = text[:s] + r + text[e:]
    return text


# ----------------------------------------------------------------------------- structure: keys, features, ids

def _find_pair(mp, key: str):
    for i, (kn, vn) in enumerate(mp.value):
        if isinstance(kn, yaml.ScalarNode) and kn.value == key:
            return i, kn, vn
    return None


def _line_rest_ok(text: str, end: int, what: str) -> int:
    """Index just after the line that holds `end`; only whitespace or a comment may follow `end` on it."""
    nl = text.find("\n", end)
    stop = len(text) if nl < 0 else nl + 1
    rest = text[end:stop].strip()
    if rest and not rest.startswith("#"):
        raise UnsafePatch("%s: unexpected text after the value on its line; edit it by hand" % what)
    return stop


def _insert_key_span(doc: _Doc, mp, key: str, value: Any, spans: list, what: str, after: Optional[str] = None) -> None:
    """Add `key: value` to the mapping node `mp` as its last pair (or right after the pair `after`)."""
    text = doc.text
    _guard(doc, mp, what)
    pairs = mp.value
    if _find_pair(mp, key) is not None:
        raise PatchError("%s: the mapping already has the key %r" % (what, key))
    r = render(value)
    if mp.flow_style:
        if not pairs:
            spans.append((mp.start_mark.index, mp.end_mark.index, "{ %s: %s }" % (key, r)))
            return
        hit = _find_pair(mp, after) if after else None
        vn = (hit[2] if hit else pairs[-1][1])
        spans.append((vn.end_mark.index, vn.end_mark.index, ", %s: %s" % (key, r)))
        return
    if not pairs:
        raise UnsafePatch("%s is an empty block mapping; edit it by hand" % what)
    hit = _find_pair(mp, after) if after else None
    kn, vn = (hit[1], hit[2]) if hit else pairs[-1]
    stop = _line_rest_ok(text, _last_end(vn), what)
    eol = _eol(text)
    line = "%s%s: %s" % (" " * kn.start_mark.column, key, r)
    if stop == len(text) and not text.endswith("\n"):
        spans.append((stop, stop, eol + line))
    else:
        spans.append((stop, stop, line + eol))


def _delete_key_span(doc: _Doc, mp, key: str, spans: list, what: str) -> None:
    text = doc.text
    _guard(doc, mp, what)
    hit = _find_pair(mp, key)
    if hit is None:
        raise PathError("%s: no key %r" % (what, key))
    i, kn, vn = hit
    _guard_subtree(doc, vn, what)
    pairs = mp.value
    n = len(pairs)
    if mp.flow_style:
        s, e = kn.start_mark.index, vn.end_mark.index
        if n == 1:
            j = e
            while j < len(text) and text[j] in " \t":
                j += 1
            if j < len(text) and text[j] == ",":
                e = j + 1
            spans.append((s, e, ""))
        elif i < n - 1:
            if not re.fullmatch(r"[ \t]*,[ \t\r\n]*", text[e:pairs[i + 1][0].start_mark.index]):
                raise UnsafePatch("%s: comment or odd separator next to the key; edit it by hand" % what)
            spans.append((s, pairs[i + 1][0].start_mark.index, ""))
        else:
            pe = pairs[i - 1][1].end_mark.index
            if not re.fullmatch(r"[ \t]*,[ \t\r\n]*", text[pe:s]):
                raise UnsafePatch("%s: comment or odd separator next to the key; edit it by hand" % what)
            spans.append((pe, e, ""))
        return
    ls = text.rfind("\n", 0, kn.start_mark.index) + 1
    if text[ls:kn.start_mark.index].strip():
        raise UnsafePatch("%s: the key shares its line with other text (first key of a list item); edit it by hand" % what)
    stop = _line_rest_ok(text, _last_end(vn), what)
    spans.append((ls, stop, ""))


def _expect(py, loc, mutate):
    import copy
    new = copy.deepcopy(py)
    mutate(_py_get(new, loc))
    return new


def _ins_key(d: dict, key: str, value: Any, after: Optional[str]) -> None:
    import copy
    items = list(d.items())
    d.clear()
    placed = False
    for k, v in items:
        d[k] = v
        if after is not None and k == after:
            d[key] = copy.deepcopy(value)
            placed = True
    if not placed:
        d[key] = copy.deepcopy(value)


def _commit_spans(text: str, spans: list, expected: Any, what: str) -> str:
    new = _apply_spans(text, spans) if spans else text
    if spans:
        try:
            got = _doc(new).py
        except PatchError as e:
            raise VerifyError("%s: patched text does not parse: %s" % (what, str(e).replace("\n", " ")))
        if not _same(got, expected):
            raise VerifyError("%s: patched text differs from the requested change at %s" % (what, _diff_path(expected, got)))
    return new


def namespace(py: Any) -> set:
    """Every id in use: team bases (`base_<team>`), feature ids of the sequence sections, `points` names."""
    out = set()
    if not isinstance(py, dict):
        return out
    teams = py.get("teams")
    if isinstance(teams, dict):
        out.update("base_%s" % t for t in teams)
    for sect in SEQ_SECTIONS:
        for it in py.get(sect) or []:
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                out.add(it["id"])
    if isinstance(py.get("points"), dict):
        out.update(k for k in py["points"] if isinstance(k, str))
    return out


def new_id(py: Any, base: str, avoid=(), sep: str = "_") -> str:
    """`<base>_<n>` (points: `p<n>`) with n one above the largest n in use for that base; never an id in use or in `avoid`."""
    base = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "f"
    if not base[0].isalpha():
        base = "f" + base
    base = base[:40]
    used = namespace(py) | set(avoid)
    pat = re.compile(r"^%s%s(\d+)$" % (re.escape(base), re.escape(sep)))
    n = 1 + max([int(m.group(1)) for m in (pat.match(u) for u in used) if m] + [0])
    while "%s%s%d" % (base, sep, n) in used:
        n += 1
    return "%s%s%d" % (base, sep, n)


def reference_sites(py: Any, name: str) -> List[dict]:
    """Every place that references `name`: [{"path", "feature", "kind"}] with `kind` "point" (a region/crossing `at`, a
    `points`/`path` item) or "join" (a line's `join`). `path` is a specpatch path."""
    out = []  # type: List[dict]
    if not isinstance(py, dict):
        return out
    for sect in ("regions", "lines", "crossings"):
        for f in py.get(sect) or []:
            if not isinstance(f, dict) or not isinstance(f.get("id"), str):
                continue
            fid = f["id"]
            if isinstance(f.get("at"), str) and f["at"] == name:
                out.append({"path": "%s.%s.at" % (sect, fid), "feature": fid, "kind": "point"})
            for key in ("points", "path"):
                v = f.get(key)
                if isinstance(v, list):
                    for j, x in enumerate(v):
                        if isinstance(x, str) and x == name:
                            out.append({"path": "%s.%s.%s[%d]" % (sect, fid, key, j), "feature": fid, "kind": "point"})
            if sect == "lines" and isinstance(f.get("join"), str) and f["join"] == name:
                out.append({"path": "lines.%s.join" % fid, "feature": fid, "kind": "join"})
    return out


def referrers(text: str, name: str) -> List[dict]:
    return reference_sites(parsed_document(text), name)


def _target_xy(py: Any, name: str) -> Optional[list]:
    for t, tv in (py.get("teams") or {}).items():
        if "base_%s" % t == name and isinstance(tv, dict):
            return tv.get("base")
    if isinstance(py.get("points"), dict) and name in py["points"]:
        return py["points"][name]
    for p in py.get("pois") or []:
        if isinstance(p, dict) and p.get("id") == name:
            return p.get("at")
    return None


def _order_item(value: dict, fid: str) -> dict:
    out = {"id": fid}
    for k in ("type", "shape"):
        if k in value and k != "id":
            out[k] = value[k]
    for k, v in value.items():
        if k not in out and k != "id":
            out[k] = v
    return out


def _render_map(value: dict, spaced: bool) -> str:
    if spaced:
        return render(value)
    return "{" + ", ".join("%s: %s" % (k, render(x)) for k, x in value.items()) + "}"


def _seq_indent(doc: _Doc) -> str:
    if isinstance(doc.root, yaml.MappingNode):
        for _, vn in doc.root.value:
            if isinstance(vn, yaml.SequenceNode) and not vn.flow_style and vn.value:
                try:
                    return _block_item_lines(doc, vn.value[0])[2]
                except UnsafePatch:
                    pass
    return "  "


def _create_section(doc: _Doc, key: str, body_line: str, spans: list) -> Optional[str]:
    """Add the top-level `key` with one body line. Returns the key it was placed after (None: last). The section goes
    before the first of analysis / output / auto_crossings when present, else at the end of the file."""
    text = doc.text
    root = doc.root
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise UnsafePatch("the document root is not a block mapping; edit it by hand")
    _guard(doc, root, "the document root")
    eol = _eol(text)
    block = "%s:%s%s" % (key, eol, body_line)
    late = ("analysis", "output", "auto_crossings")
    for i, (kn, vn) in enumerate(root.value):
        if kn.value in late and i > 0:
            pk, pv = root.value[i - 1]
            stop = _line_rest_ok(text, _last_end(pv), "the section before %s" % kn.value)
            spans.append((stop, stop, eol + block + eol))
            return pk.value
    if text.endswith("\n"):
        spans.append((len(text), len(text), eol + block + eol))
    else:
        spans.append((len(text), len(text), eol + eol + block + eol))
    return None


def _apply_put(text: str, op: Put) -> str:
    doc = _doc(text)
    tokens = _tokens(op.path)
    if tokens[-1][0] != "key":
        raise PathError("%s: a put path ends with a key" % op.path)
    node, loc, _ = _resolve(doc, tokens[:-1], op.path)
    if not isinstance(node, yaml.MappingNode):
        raise PathError("%s: the parent is not a mapping" % op.path)
    key = tokens[-1][1]
    if _find_pair(node, key) is not None:
        return _apply_op(text, Set(op.path, op.value))
    spans = []  # type: list
    _insert_key_span(doc, node, key, op.value, spans, op.path, op.after)
    expected = _expect(doc.py, loc, lambda d: _ins_key(d, key, op.value, op.after))
    return _commit_spans(text, spans, expected, op.path)


def _apply_add(text: str, op: Add) -> str:
    import copy
    doc = _doc(text)
    py = doc.py
    if not isinstance(py, dict) or not isinstance(doc.root, yaml.MappingNode):
        raise PatchError("the document is not a mapping")
    sect = op.section
    if sect not in ADD_SECTIONS:
        raise PatchError("unknown section %r (%s)" % (sect, ", ".join(ADD_SECTIONS)))
    used = namespace(py) | op.avoid
    what = "add %s" % sect
    hit = _find_pair(doc.root, sect)
    if sect == "points":
        if not (isinstance(op.value, list) and len(op.value) == 2):
            raise PatchError("a points entry is a coordinate pair [x, y]")
        fid = op.id or new_id(py, "p", op.avoid, sep="")
    else:
        if not isinstance(op.value, dict):
            raise PatchError("a %s entry is a mapping" % sect)
        fid = op.id or op.value.get("id")
        if fid is None:
            base = "poi" if sect == "pois" else op.value.get("type")
            if not isinstance(base, str):
                raise PatchError("%s: give an id or a type to derive it from" % what)
            fid = new_id(py, base, op.avoid)
    if not schema._valid_id(fid):
        raise PatchError("%s: invalid id %r (must match [a-z][a-z0-9_]*, at most %d characters)" % (what, fid, schema.ID_MAX_LEN))
    if fid in used:
        raise PatchError("%s: the id %r is already in use" % (what, fid))
    if isinstance(op.value, dict) and op.value.get("id") not in (None, fid):
        raise PatchError("%s: the value's id differs from the requested id" % what)
    op.result = {"id": fid}
    spans = []  # type: list
    if sect == "points":
        item = op.value
        if hit is not None and isinstance(py.get("points"), dict):
            return _apply_op(text, Put("points.%s" % fid, item))
        after = _create_section(doc, "points", "%s%s: %s" % (_seq_indent(doc), fid, render(item)), spans)
        expected = _expect(py, [], lambda d: _ins_key(d, "points", {fid: item}, after))
        return _commit_spans(text, spans, expected, what)
    item = _order_item(op.value, fid)
    if hit is None:
        after = _create_section(doc, sect, "%s- %s" % (_seq_indent(doc), render(item)), spans)
        expected = _expect(py, [], lambda d: _ins_key(d, sect, [item], after))
        return _commit_spans(text, spans, expected, what)
    seq = hit[2]
    if not isinstance(seq, yaml.SequenceNode):
        raise UnsafePatch("%s is not a list; edit it by hand" % sect)
    _guard(doc, seq, what)
    fmt = None
    if not seq.flow_style and seq.value:
        last = seq.value[-1]
        if isinstance(last, yaml.MappingNode) and last.flow_style:
            spaced = text[last.start_mark.index:last.start_mark.index + 2] == "{ "
            fmt = lambda ind, eo: "%s- %s" % (ind, _render_map(item, spaced))
        elif isinstance(last, yaml.MappingNode):
            col = last.start_mark.column

            def fmt(ind, eo, _item=item, _col=col):
                rows = ["%s: %s" % (k, render(v)) for k, v in _item.items()]
                return (ind + "- " + rows[0]) + "".join(eo + " " * _col + r for r in rows[1:])
    _insert_span(doc, seq, None, item, spans, what, fmt)
    expected = _expect(py, [sect], lambda lst: lst.append(copy.deepcopy(item)))
    return _commit_spans(text, spans, expected, what)


def _apply_delete_named(text: str, op: Delete) -> str:
    doc = _doc(text)
    tokens = _tokens(op.path)
    what = op.path
    if len(tokens) == 2 and tokens[0] == ("key", "teams"):
        raise PatchError("%s: a team and its base cannot be deleted" % what)
    feature = (len(tokens) == 2 and tokens[0][0] == "key" and tokens[0][1] in ADD_SECTIONS and tokens[1][0] == "key")
    if not feature:
        node, loc, _ = _resolve(doc, tokens[:-1], what)
        if not isinstance(node, yaml.MappingNode):
            raise PathError("%s: the parent is not a mapping" % what)
        spans = []  # type: list
        _delete_key_span(doc, node, tokens[-1][1], spans, what)
        expected = _expect(doc.py, loc, lambda d: d.pop(tokens[-1][1]))
        return _commit_spans(text, spans, expected, what)
    sect, name = tokens[0][1], tokens[1][1]
    _resolve(doc, tokens, what)   # existence and id uniqueness
    py = doc.py
    sites = [s for s in reference_sites(py, name) if s["feature"] != name]
    op.result = {"deleted": name, "detached": []}
    if sites:
        if not op.detach_refs:
            raise ReferencedError(name, sites)
        xy = _target_xy(py, name)
        sub = []  # type: list
        for st in sites:
            if st["kind"] == "join":
                sub.append(Delete(st["path"]))
                line = next(l for l in py["lines"] if l.get("id") == st["feature"])
                if "join_end" in line:
                    sub.append(Delete("lines.%s.join_end" % st["feature"]))
            else:
                sub.append(Set(st["path"], list(xy), detach=True))
        text = apply_ops(text, sub)
        doc = _doc(text)
        op.result["detached"] = [s["path"] for s in sites]
        py = doc.py
    spans = []  # type: list
    root = doc.root
    if sect == "points":
        pnode = _find_pair(root, "points")[2]
        if len(pnode.value) == 1 and not pnode.flow_style:
            _delete_key_span(doc, root, "points", spans, what)
            expected = _expect(py, [], lambda d: d.pop("points"))
        else:
            _delete_key_span(doc, pnode, name, spans, what)
            expected = _expect(py, ["points"], lambda d: d.pop(name))
    else:
        seq = _find_pair(root, sect)[2]
        idx = [i for i, it in enumerate(seq.value) if _item_id(it) == name][0]
        if len(seq.value) == 1 and not seq.flow_style:
            _delete_key_span(doc, root, sect, spans, what)   # the only entry: the section goes with it
            expected = _expect(py, [], lambda d: d.pop(sect))
        else:
            _delete_span(doc, seq, idx, spans, what)
            expected = _expect(py, [sect], lambda lst: lst.pop(idx))
    return _commit_spans(text, spans, expected, what)


def _apply_rename(text: str, op: Rename) -> str:
    doc = _doc(text)
    py = doc.py
    old, new = op.old, op.new
    what = "rename %s" % old
    if not schema._valid_id(new):
        raise PatchError("%s: invalid id %r (must match [a-z][a-z0-9_]*, at most %d characters)" % (what, new, schema.ID_MAX_LEN))
    if old == new:
        return text
    if new in namespace(py) or new in op.avoid:
        raise PatchError("%s: the id %r is already in use" % (what, new))
    teams = py.get("teams") if isinstance(py, dict) else None
    if isinstance(teams, dict) and any(old == "base_%s" % t for t in teams):
        raise PatchError("%s: a team base id is derived from the team id" % what)
    defs = []  # type: List[str]
    for sect in SEQ_SECTIONS:
        defs.extend(sect for it in py.get(sect) or [] if isinstance(it, dict) and it.get("id") == old)
    if isinstance(py.get("points"), dict) and old in py["points"]:
        defs.append("points")
    if not defs:
        raise PathError("%s: no feature with that id" % what)
    if len(defs) > 1:
        raise UnsafePatch("%s: the id is defined %d times (ambiguous); fix the duplicates by hand" % (what, len(defs)))
    sect = defs[0]
    sub = []  # type: list
    sites = reference_sites(py, old)
    for st in sites:
        sub.append(Set(st["path"], new))
    pinned = None
    if sect in ("regions", "lines", "scatter"):
        item = next(it for it in py[sect] if it.get("id") == old)
        if "seed" not in item:
            pinned = schema.feature_seed(old)
            sub.append(Put("%s.%s.seed" % (sect, old), pinned, after="id"))
    if sect != "points":
        sub.append(Set("%s.%s.id" % (sect, old), new))
    text2 = apply_ops(text, sub) if sub else text
    if sect == "points":
        doc2 = _doc(text2)
        pnode = _find_pair(doc2.root, "points")[2]
        _, kn, _ = _find_pair(pnode, old)
        _guard(doc2, pnode, what)
        if id(kn) in doc2.flagged:
            raise UnsafePatch("%s: the key carries an anchor" % what)
        spans = [(kn.start_mark.index, kn.end_mark.index, _fmt_str(new))]

        def mut(d):
            items = list(d.items())
            d.clear()
            for k, v in items:
                d[new if k == old else k] = v
        text2 = _commit_spans(text2, spans, _expect(doc2.py, ["points"], mut), what)
    op.result = {"renamed": [old, new], "references": [s["path"] for s in sites], "seed_pinned": pinned}
    return text2


# ----------------------------------------------------------------------------- reference awareness

def _is_point_pair(v: Any) -> bool:
    return (isinstance(v, list) and len(v) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v))


def _ref_definition(doc: _Doc, name: str) -> Optional[str]:
    py = doc.py if isinstance(doc.py, dict) else {}
    teams = py.get("teams")
    if isinstance(teams, dict):
        for t in teams:
            if "base_%s" % t == name:
                return "teams.%s.base" % t
    pts = py.get("points")
    if isinstance(pts, dict) and name in pts:
        return "points.%s" % name
    for p in py.get("pois") or []:
        if isinstance(p, dict) and p.get("id") == name:
            return "pois.%s.at" % name
    return None


def _in_reference_place(tokens) -> bool:
    if tokens[-1] == ("key", "at"):
        return True
    return (len(tokens) >= 2 and tokens[-1][0] == "idx" and tokens[-2][0] == "key"
            and tokens[-2][1] in ("path", "points"))


def _expected(py, op_kind: str, loc, value=None, index=None):
    """Return a deep copy of `py` with the requested change applied (independent of the text spans)."""
    import copy
    new = copy.deepcopy(py)
    if op_kind == "set":
        if not loc:
            return value
        _py_get(new, loc[:-1])[loc[-1]] = copy.deepcopy(value)
    elif op_kind == "insert":
        seq = _py_get(new, loc)
        seq.insert(len(seq) if index is None else index, copy.deepcopy(value))
    else:
        del _py_get(new, loc[:-1])[loc[-1]]
    return new


def _apply_op(text: str, op) -> str:
    if isinstance(op, Add):
        return _apply_add(text, op)
    if isinstance(op, Rename):
        return _apply_rename(text, op)
    if isinstance(op, Put):
        return _apply_put(text, op)
    if isinstance(op, Delete) and _tokens(op.path)[-1][0] == "key":
        return _apply_delete_named(text, op)
    doc = _doc(text)
    what = op.path
    tokens = _tokens(op.path)
    node, loc, parent = _resolve(doc, tokens, what)
    spans = []  # type: list
    if isinstance(op, Set):
        if tokens[-1][0] == "ins":
            raise PathError("%s: use an insertion value without Set" % what)
        old = _py_get(doc.py, loc)
        if (not op.detach and isinstance(node, yaml.ScalarNode) and isinstance(old, str)
                and _is_point_pair(op.value) and _in_reference_place(tokens)):
            target = _ref_definition(doc, old)
            if target is not None:
                tt = _tokens(target)
                node, loc, parent = _resolve(doc, tt, target)
                old = _py_get(doc.py, loc)
                what = "%s (reference, moves %s)" % (what, target)
        _guard_subtree(doc, node, what)
        _assign(doc, node, old, op.value, spans, what)
        expected = _expected(doc.py, "set", loc, op.value)
    elif isinstance(op, Insert):
        if tokens[-1][0] != "ins":
            raise PathError("%s: an insert path ends with [+n] or [+]" % what)
        _guard(doc, parent, what)
        _insert_span(doc, parent, tokens[-1][1], op.value, spans, what)
        expected = _expected(doc.py, "insert", loc, op.value, tokens[-1][1])
    else:
        if tokens[-1][0] != "idx":
            raise PathError("%s: a delete path ends with [n]" % what)
        _guard(doc, parent, what)
        _delete_span(doc, parent, tokens[-1][1], spans, what)
        expected = _expected(doc.py, "delete", loc)
    new = _apply_spans(text, spans) if spans else text
    if spans:
        try:
            got = _doc(new).py   # also primes the cache for the next op on the result
        except PatchError as e:
            raise VerifyError("%s: patched text does not parse: %s" % (what, str(e).replace("\n", " ")))
        if not _same(got, expected):
            raise VerifyError("%s: patched text differs from the requested change at %s" % (
                what, _diff_path(expected, got)))
    return new


# ----------------------------------------------------------------------------- public API

def apply_ops(text: str, ops) -> str:
    """Apply `ops` in order to the spec text; each op is verified against the document it was applied to.
    Raises a PatchError subclass and returns nothing changed on any refusal."""
    for op in ops:
        text = _apply_op(text, op)
    return text


def patch_text(text: str, ops, spec_path: Optional[str] = None, palette_path: Optional[str] = None) -> str:
    """apply_ops, then run the real loader on the result. A loader failure raises `schema.SpecError` (its error list)
    and the caller's original text is untouched."""
    new = apply_ops(text, ops)
    schema.load_spec_text(new.encode("utf-8"), spec_path, palette_path)
    return new


def write_atomic(path: str, text: str) -> None:
    """Write `text` (UTF-8, no newline translation) through a temp file in the same folder and os.replace."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".specset-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        try:
            import shutil
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

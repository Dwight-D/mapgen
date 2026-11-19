"""Span-patch engine: edit values of a map spec as exact text spans, leaving every other byte untouched.

The spec text is composed with the loader's YAML semantics (`schema._SpecLoader`) to get node marks; an edit is
a list of (start, end, replacement) spans applied to the original string. Comments, quoting, indentation, key order
and line endings outside the spans are preserved by construction. Every patch is verified: the patched text is
re-parsed and must equal the old document with exactly the requested change applied; `patch_text` then runs the
real loader (`schema.load_spec_text`) on the result. Contract: MAPGEN_SPEC.md "spec set", README.md.

Path grammar: `seg(.seg | [n])*`. `seg` is `[A-Za-z_][A-Za-z0-9_-]*`: a mapping key, or, when the current node is a
sequence of mappings, the `id` of one item (`pois.fa.at`, `lines.river_main.path[2]`). `[n]` is a zero-based position
in a sequence that has no ids (a waypoint, a vertex). `[+n]` / `[+]` (last token only) is an insertion point.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
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
    """Delete the sequence item at `path` (the last token is `[n]`)."""

    def __init__(self, path: str):
        self.path = path


DELETE_TOKEN = "@delete"
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


def parse_op(arg: str, detach: bool = False):
    """`path=value` -> Set; `path[+n]=value` -> Insert; `path[n]=@delete` -> Delete."""
    if "=" not in arg:
        raise PatchError("expected <path>=<value>, got %r" % arg)
    path, raw = arg.split("=", 1)
    toks = _tokens(path)
    if raw.strip() == DELETE_TOKEN:
        if toks[-1][0] != "idx":
            raise PathError("%s needs a sequence position as the last token ([n]): %r" % (DELETE_TOKEN, path))
        return Delete(path)
    value = parse_value(raw)
    if toks[-1][0] == "ins":
        return Insert(path, toks[-1][1], value)
    return Set(path, value, detach)


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


class _Doc:
    def __init__(self, text: str):
        self.text = text
        loader = _MarkLoader(text)
        try:
            self.root = loader.get_single_node()
        except yaml.YAMLError as e:
            raise PatchError("cannot parse the spec: %s" % str(e).replace("\n", " "))
        finally:
            self.flagged = loader.flagged
            loader.dispose()
        if self.root is None:
            raise PatchError("empty document")
        try:
            self.py = yaml.load(text, Loader=schema._SpecLoader)
        except yaml.YAMLError as e:
            raise UnsafePatch("the spec does not parse (%s)" % str(e).replace("\n", " "))


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
    if isinstance(node, yaml.MappingNode):
        seen = set()
        for kn, _ in node.value:
            if kn.tag == "tag:yaml.org,2002:merge":
                raise UnsafePatch("%s is in a mapping with a merge key (<<); edit it by hand" % what)
            if isinstance(kn, yaml.ScalarNode):
                if kn.value in seen:
                    raise UnsafePatch("%s is in a mapping with the duplicate key %r" % (what, kn.value))
                seen.add(kn.value)


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


def _insert_span(doc: _Doc, seq, index: Optional[int], value: Any, spans: list, what: str) -> None:
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
        if idx == 0:
            ls, _, indent = _block_item_lines(doc, items[0])
            spans.append((ls, ls, "%s- %s%s" % (indent, render(value), eol)))
        else:
            _, stop, indent = _block_item_lines(doc, items[idx - 1])
            line = "%s- %s" % (indent, render(value))
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
    doc = _Doc(text)
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
            got = yaml.load(new, Loader=schema._SpecLoader)
        except yaml.YAMLError as e:
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

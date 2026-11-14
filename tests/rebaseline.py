"""Re-baseline mapgen regression maps: build each named map into a fresh temporary directory and write its
per-file SHA-256 hashes to tests/baselines/<map>.json. Re-baselining is a deliberate step: run it only
after the owner has approved a diff report for the change. The regression suite never runs this script.
"""
import argparse
import os
import shutil
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import regress  # noqa: E402
from mapgen.schema import SpecError  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip(), prog="rebaseline.py")
    parser.add_argument("--dry-run", action="store_true", help="build and compare but write nothing")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="re-baseline every discovered map")
    group.add_argument("maps", metavar="MAP", nargs="*", default=[], help="spec name, e.g. thornwald")
    args = parser.parse_args(argv)
    if not args.maps and not args.all:
        parser.error("give MAP names or --all")

    known = regress.discover_maps()
    known_names = [n for n, _ in known]
    if not args.all:
        for name in args.maps:
            if name not in known_names:
                print("error: unknown map '%s' (known: %s)" % (name, ", ".join(known_names)))
                return 1
    selected = [(n, s) for n, s in known if args.all or n in args.maps]

    rc = 0
    built = []  # type: List[Tuple[str, str, Dict[str, str]]]
    for name, spec in selected:
        tmp = tempfile.mkdtemp(prefix="mapgen-rebaseline-")
        try:
            files = regress.build_and_hash(spec, os.path.join(tmp, "out"))
        except SpecError as e:
            print("%d error(s) in %s:" % (len(e.errors), spec))
            for line in e.errors:
                print("  " + line)
            rc = 1
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            expected = regress.load_baseline(name)
        except FileNotFoundError:
            print("%s: new baseline (%d files)" % (name, len(files)))
        else:
            problems = regress.compare(name, expected, files)
            if problems:
                print("%s: %d file(s) differ" % (name, len(problems)))
                for p in problems:
                    print("  " + p)
            else:
                print("%s: unchanged" % name)
        built.append((name, spec, files))
    # Baselines are written only after every selected map has been built.
    if not args.dry_run:
        for name, spec, files in built:
            regress.write_baseline(name, spec, files)
            print("  wrote tests/baselines/%s.json" % name)
    return rc


if __name__ == "__main__":
    sys.exit(main())

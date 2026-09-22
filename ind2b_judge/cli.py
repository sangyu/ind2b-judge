"""Command-line entry point: grade an indication2binder run.

    ind2b-judge runs/MONDO_0005061
    ind2b-judge runs/MONDO_0005061 runs/MONDO_0005105 --repo ../Boston-CompBio-Hackathon
    ind2b-judge runs/* --json report.json

Exit codes follow the rubric's verdict, so this is usable as a CI check:

    0  CORRECT or PARTIALLY CORRECT -- no P0 row failed
    1  INCORRECT                    -- at least one P0 row failed
    2  UNJUDGEABLE                  -- too little was persisted to grade

PARTIALLY CORRECT exits 0 deliberately. A run that honestly reports what it
could not verify is a passing run; the UNVERIFIABLE list is logging debt, not
a defect in the science. Failing the build on it would push a team toward
deleting the honest gaps rather than filling them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bridge import load_multi, load_run
from .harness import grade, render

EXIT = {"CORRECT": 0, "PARTIALLY CORRECT": 0, "INCORRECT": 1, "UNJUDGEABLE": 2}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ind2b-judge", description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="+", type=Path,
                    help="ind2b run directories (the ones holding stage0_disease.json)")
    ap.add_argument("--repo", type=Path, default=None,
                    help="path to the pipeline checkout, to record the commit it ran at")
    ap.add_argument("--rankings", type=Path, default=None,
                    help="JSON list of repeated ranking orders over identical "
                         "evidence; without it the determinism row stays UNVERIFIABLE")
    ap.add_argument("--json", type=Path, default=None,
                    help="write the full graded result here")
    ap.add_argument("--quiet", action="store_true", help="verdict line only")
    args = ap.parse_args(argv)

    for d in args.run_dirs:
        if not (d / "stage0_disease.json").exists():
            print(f"error: {d} does not look like an ind2b run directory "
                  "(no stage0_disease.json)", file=sys.stderr)
            return 2

    rankings = json.loads(args.rankings.read_text()) if args.rankings else None

    if len(args.run_dirs) == 1 and not rankings:
        record_set = load_run(args.run_dirs[0], repo=args.repo)
    else:
        record_set = load_multi(list(args.run_dirs), repo=args.repo,
                                rankings=rankings)

    result = grade(record_set)
    verdict = result["machine_overall"]

    if args.quiet:
        print(verdict)
    else:
        print(render(result))

    if args.json:
        args.json.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nwrote {args.json}")

    return EXIT.get(verdict, 2)


if __name__ == "__main__":
    raise SystemExit(main())

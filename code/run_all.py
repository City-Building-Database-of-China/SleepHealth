"""Run the core-model analysis pipeline for the configured cities.

Usage
-----
    python run_all.py                       # Beijing (default in this release; only Beijing raw inputs ship here)
    python run_all.py --cities Beijing,Wuhan
    python run_all.py --check               # input check only, no computation
    python run_all.py --allow-partial       # exit 0 even if some inputs/buildings failed
    python run_all.py --cities Beijing --merge
                                            # re-run only Beijing and replace Beijing's rows
                                            # in the existing six-city results (others kept)

Every problem (missing EPW, failed building, missing meter, ...) is written to
output/run_problems/*.csv. If any exist the run is reported as INCOMPLETE and
exits with code 1, so a partial result is never mistaken for a full one.
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True   # Avoid creating __pycache__ files.
import argparse
import csv
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CODE_ROOT = PROJECT_ROOT / "code"
OUTPUT_ROOT = Path(os.environ.get("MODEL_OUTPUT_ROOT", PROJECT_ROOT / "output")).resolve()
PROBLEMS_DIR = OUTPUT_ROOT / "run_problems"

CHECK_STEP = ("step0_check_inputs.py", "Step 0: Input check")
STEPS = [
    ("step1_compute_set.py", "Step 1: SET and per-capita discomfort"),
    ("step2_pivot_tables.py", "Step 2: Analysis-ready pivot tables"),
    ("step3_energy_capacity.py", "Step 3: Meter checks, capacity, and peak load"),
]


def run_step(script: str, description: str, env: dict) -> None:
    """Run one pipeline stage and stop immediately when it fails."""
    print(f"\n{'#' * 60}\n#  {description}\n{'#' * 60}")
    started = time.time()
    result = subprocess.run(
        [sys.executable, str(CODE_ROOT / script)],
        cwd=str(CODE_ROOT),
        env=env,
        check=False,
    )
    if result.returncode != 0:
        print(f"\n[ERROR] {description} failed (exit code {result.returncode}).")
        raise SystemExit(result.returncode)
    print(f"\n[OK] {description} ({time.time() - started:.0f} s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", help="Comma-separated subset, e.g. Beijing,Wuhan (default: Beijing — the only city whose raw inputs ship in this release)")
    parser.add_argument("--check", action="store_true", help="Only run the input check")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Exit with code 0 even when problems were logged")
    parser.add_argument("--merge", action="store_true",
                        help="With --cities: compute those cities separately and replace only their rows "
                             "in the existing full results (previous files are backed up)")
    args = parser.parse_args()

    env = os.environ.copy()
    env.setdefault("MODEL_CITIES", "Beijing")  # public release includes Beijing raw inputs
    if args.cities:
        env["MODEL_CITIES"] = args.cities
    if args.merge:
        if not args.cities:
            parser.error("--merge needs --cities, e.g. --cities Beijing --merge")
        return run_merge(args, env)

    print("=" * 60)
    print("  Multi-city thermal-comfort and cooling-demand model")
    print(f"  Cities: {env.get('MODEL_CITIES') or 'all (config/cities.py)'}")
    print("=" * 60)
    started = time.time()
    if PROBLEMS_DIR.is_dir():
        shutil.rmtree(PROBLEMS_DIR)   # problem logs always describe this run only
    run_step(*CHECK_STEP, env=env)
    if not args.check:
        for script, description in STEPS:
            run_step(script, description, env=env)
    return report(started, args.allow_partial)


def run_merge(args, env) -> int:
    """Re-run the selected cities into output/_partial/<cities>/, then merge into output/."""
    sys.path.insert(0, str(CODE_ROOT))
    from config.cities import selected_cities  # noqa: E402
    os.environ["MODEL_CITIES"] = args.cities
    cities = [c["en"] for c in selected_cities()]
    partial = OUTPUT_ROOT / "_partial" / "_".join(cities).lower()
    if partial.exists():
        shutil.rmtree(partial)
    penv = dict(env, MODEL_OUTPUT_ROOT=str(partial))

    print("=" * 60)
    print(f"  Re-run {', '.join(cities)} and merge into {OUTPUT_ROOT}")
    print("=" * 60)
    started = time.time()
    run_step(*CHECK_STEP, env=penv)
    run_step("step1_compute_set.py", "Step 1: SET and per-capita discomfort", env=penv)
    run_step("step3_energy_capacity.py", "Step 3: Meter checks, capacity, and peak load", env=penv)

    global PROBLEMS_DIR
    main_problems = PROBLEMS_DIR
    PROBLEMS_DIR = partial / "run_problems"
    status = report(started, allow_partial=True)
    n_problems = sum(1 for _ in PROBLEMS_DIR.glob("*_problems.csv")) if PROBLEMS_DIR.is_dir() else 0
    if n_problems and not args.allow_partial:
        print(f"\n[STOP] problems in the re-run of {', '.join(cities)} — nothing merged. "
              f"Fix them (see {PROBLEMS_DIR}) or add --allow-partial to merge anyway.")
        return 1

    print(f"\n{'#' * 60}\n#  Merge into the full results\n{'#' * 60}")
    from merge_city_results import merge  # noqa: E402
    merge(partial, cities, OUTPUT_ROOT)
    if PROBLEMS_DIR.is_dir():   # keep this re-run's problem logs next to the main ones
        main_problems.mkdir(parents=True, exist_ok=True)
        for f in PROBLEMS_DIR.glob("*_problems.csv"):
            shutil.copy2(f, main_problems / f"merge_{'_'.join(cities).lower()}_{f.name}")
    menv = dict(env)
    menv.pop("MODEL_CITIES", None)
    run_step("step2_pivot_tables.py", "Step 2: Pivot tables (rebuilt on merged results)", env=menv)
    print(f"\n  Merged {', '.join(cities)} into {OUTPUT_ROOT} ({time.time() - started:.0f} s)")
    return status


def report(started: float, allow_partial: bool) -> int:
    """Summarise output/run_problems/*.csv; non-zero exit if anything went wrong."""
    files = sorted(glob.glob(str(PROBLEMS_DIR / "*_problems.csv")))
    counts = {}
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            counts[Path(f).name] = max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    total = sum(counts.values())
    print(f"\n{'=' * 60}")
    if total:
        print(f"  INCOMPLETE: {total} problem(s) logged ({time.time() - started:.0f} s)")
        for name, n in counts.items():
            print(f"    {n:>5}  {PROBLEMS_DIR / name}")
        print("  Open these CSV files to see which city / scenario / strategy / building is affected.")
    else:
        print(f"  Completed with no problems in {time.time() - started:.0f} s")
    print(f"{'=' * 60}")
    return 0 if (total == 0 or allow_partial) else 1


if __name__ == "__main__":
    raise SystemExit(main())

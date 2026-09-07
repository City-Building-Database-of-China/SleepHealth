"""Run the SleepHealth quick demo on a small Beijing subset.

The demo reuses the released three-stage pipeline. It does not reproduce
manuscript city-level results or figures.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = Path(__file__).resolve().parent
DATA_ROOT = DEMO_ROOT / "data"
OUTPUT_ROOT = DEMO_ROOT / "output"
EPW_ROOT = REPO_ROOT / "code" / "data" / "epw"
RUNNER = REPO_ROOT / "code" / "run_all.py"


def main() -> int:
    if not RUNNER.is_file():
        print(f"[ERROR] Pipeline runner not found: {RUNNER}")
        return 2
    if not (DATA_ROOT / "energyplus_outputs" / "IndoorEnv" / "2020").is_dir():
        print(f"[ERROR] Demo IndoorEnv data not found under: {DATA_ROOT}")
        return 2
    if not EPW_ROOT.is_dir():
        print(f"[ERROR] Beijing EPW directory not found: {EPW_ROOT}")
        return 2

    env = os.environ.copy()
    env["MODEL_DATA_ROOT"] = str(DATA_ROOT)
    env["MODEL_OUTPUT_ROOT"] = str(OUTPUT_ROOT)
    env["BASE_EPW"] = str(EPW_ROOT)

    print("=" * 60, flush=True)
    print("  SleepHealth quick demo", flush=True)
    print("  One Beijing archetype, 2020 scenario", flush=True)
    print("=" * 60, flush=True)
    print(f"  Data   : {DATA_ROOT}", flush=True)
    print(f"  Output : {OUTPUT_ROOT}", flush=True)
    print(f"  EPW    : {EPW_ROOT}", flush=True)
    print(flush=True)
    print("  These files are a small real subset of the released Beijing", flush=True)
    print("  EnergyPlus outputs. They demonstrate the processing workflow", flush=True)
    print("  and are not used to reproduce manuscript results.", flush=True)
    print(flush=True)

    started = time.time()
    result = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    elapsed = time.time() - started
    if result.returncode != 0:
        print(f"\n[ERROR] Demo failed (exit code {result.returncode}) after {elapsed:.1f} s.")
        return result.returncode

    expected = [
        OUTPUT_ROOT / "set_calculations" / "summary_uncomfortable_hours.csv",
        OUTPUT_ROOT / "per_capita_hours" / "per_capita_hours_summary.csv",
        OUTPUT_ROOT / "pivot_tables" / "total_uncomfortable_hours_pivot.xlsx",
        OUTPUT_ROOT / "pivot_tables" / "per_capita_hours_pivot.xlsx",
        OUTPUT_ROOT / "energy_capacity" / "energy_capacity_summary.csv",
        OUTPUT_ROOT / "energy_capacity" / "coincident_peak_load.csv",
    ]
    missing = [path for path in expected if not path.is_file()]
    if missing:
        print("\n[ERROR] Demo finished but expected output files are missing:")
        for path in missing:
            print(f"  - {path}")
        return 3

    print(f"\n[OK] Demo completed in {elapsed:.1f} s.")
    print("Expected output files:")
    for path in expected:
        print(f"  - {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

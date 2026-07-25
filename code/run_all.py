"""Run the three-stage core-model analysis pipeline."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CODE_ROOT = PROJECT_ROOT / "code"

STEPS = [
    ("step1_compute_set.py", "Step 1: SET and per-capita discomfort"),
    ("step2_pivot_tables.py", "Step 2: Analysis-ready pivot tables"),
    ("step3_energy_capacity.py", "Step 3: Cooling energy, capacity, and peak load"),
]


def run_step(script: str, description: str) -> None:
    """Run one pipeline stage and stop immediately when it fails."""
    print(f"\n{'#' * 60}\n#  {description}\n{'#' * 60}")
    started = time.time()
    result = subprocess.run(
        [sys.executable, str(CODE_ROOT / script)],
        cwd=str(CODE_ROOT),
        check=False,
    )
    if result.returncode != 0:
        print(f"\n[ERROR] {description} failed (exit code {result.returncode}).")
        raise SystemExit(result.returncode)
    print(f"\n[OK] {description} ({time.time() - started:.0f} s)")


def main() -> int:
    print("=" * 60)
    print("  Beijing public-reproduction thermal-comfort and cooling-demand model")
    print("=" * 60)
    started = time.time()
    for script, description in STEPS:
        run_step(script, description)
    print(f"\n{'=' * 60}")
    print(f"  Completed in {time.time() - started:.0f} s")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

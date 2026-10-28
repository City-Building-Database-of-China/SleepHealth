"""Machine-readable problem log shared by all steps.

Each step collects problems with ``add_problem`` and writes them once with
``write_problems`` to ``output/run_problems/<step>_problems.csv``. The file is
deleted at the start of the step (``reset_problems``), so it always describes
the latest run. ``run_all.py`` reads every file at the end and reports the run
as INCOMPLETE (exit code 1) when any problem was logged.
"""

from __future__ import annotations

import os

import pandas as pd

COLUMNS = ['City', 'Scenario', 'Strategy', 'Object', 'Problem']
_PROBLEMS: list[dict] = []


def problems_dir(output_root) -> str:
    return os.path.join(str(output_root), "run_problems")


def problems_path(output_root, step: str) -> str:
    return os.path.join(problems_dir(output_root), f"{step}_problems.csv")


def reset_problems(output_root, step: str) -> None:
    _PROBLEMS.clear()
    path = problems_path(output_root, step)
    if os.path.exists(path):
        os.remove(path)


def add_problem(city="", scenario="", strategy="", item="", problem="") -> None:
    _PROBLEMS.append(dict(zip(COLUMNS, [city, scenario, strategy, item, problem])))


def problems() -> list[dict]:
    return list(_PROBLEMS)


def write_problems(output_root, step: str) -> int:
    """Write the collected problems; return how many there were."""
    if not _PROBLEMS:
        return 0
    os.makedirs(problems_dir(output_root), exist_ok=True)
    path = problems_path(output_root, step)
    pd.DataFrame(_PROBLEMS, columns=COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n  [WARN] {len(_PROBLEMS)} problem(s) written to {path}", flush=True)
    return len(_PROBLEMS)


def remove_stale(*paths) -> None:
    """Delete previous-run outputs so a failed step cannot leave old files behind."""
    for path in paths:
        if path and os.path.isfile(path):
            os.remove(path)

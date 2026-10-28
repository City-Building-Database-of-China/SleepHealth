"""Portable paths for the three-stage core-model pipeline.

The release uses a layered project layout. By default, every path is resolved
relative to the folder that contains ``run_all.py``. Environment variables may
optionally override the data root, output root, or an individual input folder.
"""

from __future__ import annotations

import os
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CODE_ROOT.parent
DATA_ROOT = Path(os.environ.get("MODEL_DATA_ROOT", PROJECT_ROOT / "data")).resolve()
OUTPUT_ROOT = Path(os.environ.get("MODEL_OUTPUT_ROOT", PROJECT_ROOT / "output")).resolve()

ENERGYPLUS_OUTPUT_ROOT = DATA_ROOT / "energyplus_outputs"
SUPPORTING_DATA_ROOT = DATA_ROOT / "supporting_data"

INDOOR_ROOT = Path(
    os.environ.get("BASE_INDOOR", ENERGYPLUS_OUTPUT_ROOT / "IndoorEnv")
).resolve()
ENERGY_ROOT = Path(
    os.environ.get("BASE_ENERGY", ENERGYPLUS_OUTPUT_ROOT / "Energy")
).resolve()
CAPACITY_ROOT = Path(
    os.environ.get("BASE_CAPACITY", ENERGYPLUS_OUTPUT_ROOT / "Capacity")
).resolve()
EPW_ROOT = Path(
    os.environ.get("BASE_EPW", DATA_ROOT / "epw")
).resolve()
CLUSTER_MAP_ROOT = Path(
    os.environ.get("BASE_CLUSTER", SUPPORTING_DATA_ROOT / "ClusterMap")
).resolve()
POPULATION_ROOT = Path(
    os.environ.get("BASE_POP", SUPPORTING_DATA_ROOT / "Population")
).resolve()


def as_string(path: Path) -> str:
    """Return a path as a string for libraries that expect string paths."""
    return str(path)

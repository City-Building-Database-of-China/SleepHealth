"""Discover EnergyPlus result files across flat and legacy directory layouts.

Supported examples
------------------
Flat, year/scenario-first layout::

    IndoorEnv/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0.csv
    Energy/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0-meter.csv
    Capacity/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0-table.htm

Legacy nested layout::

    IndoorEnv/S1a_2020_Evening27/bei3jing1shi4/2020/<building>.csv

The discovery layer keeps Beijing first, then processes every other recognised
city that is actually present.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


ZERO_BASED_STRATEGY_MAP = {
    "S0": "S1 (Baseline_Evening27)",
    "S1": "S2 (FixedCap_AllDay_32_27)",
    "S2": "S3 (FixedCap_Evening26)",
    "S3": "S4 (FixedCap_AllDay_32_26)",
    "S4": "S5 (AutoSize_AllDay_32_26)",
}

ONE_BASED_STRATEGY_MAP = {
    "S1": "S1 (Baseline_Evening27)",
    "S2": "S2 (FixedCap_AllDay_32_27)",
    "S3": "S3 (FixedCap_Evening26)",
    "S4": "S4 (FixedCap_AllDay_32_26)",
    "S5": "S5 (AutoSize_AllDay_32_26)",
}


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "", str(text).lower())


def match_scenario(parts: Iterable[str], scenario_folders: dict[str, str]):
    """Return ``(folder_token, manuscript_label)`` from path components."""
    parts = [str(p) for p in parts]
    for part in parts:
        key = _norm(part)
        if key in {"2020", "2020baseline"}:
            return part, "2020 Baseline"
        for folder, label in scenario_folders.items():
            if _norm(folder) == key or _norm(label) == key:
                return part, label
    return None, None


def detect_city(path: Path, city_order: list[str]):
    """Detect city from a filename prefix first, then from path components."""
    name = path.name
    for city in city_order:
        if name.startswith(f"{city}_") or name.startswith(city):
            return city
    for part in path.parts:
        if part in city_order:
            return part
    return None


def extract_building_id(path: Path) -> str:
    """Strip known result suffixes while preserving the strategy code."""
    name = path.name
    for suffix in ("-meter.csv", "-table.htm", "-table.html", ".csv"):
        if name.lower().endswith(suffix.lower()):
            return name[: -len(suffix)]
    return path.stem


def extract_strategy_code(building_id: str):
    match = re.search(r"_(S\d+)$", building_id, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _path_strategy_label(path: Path, strategy_specs: list[dict]):
    normalized_parts = {_norm(part) for part in path.parts}
    for spec in strategy_specs:
        for key in ("d2020", "dfuture"):
            folder = spec.get(key)
            if folder and _norm(folder) in normalized_parts:
                return spec["label"]
    return None


def _infer_index_base(candidates: list[dict]) -> int:
    override = os.environ.get("FLAT_STRATEGY_INDEX_BASE", "auto").strip().lower()
    if override in {"0", "zero", "zero_based"}:
        return 0
    if override in {"1", "one", "one_based"}:
        return 1

    future_codes = {
        row["strategy_code"]
        for row in candidates
        if row["scenario_label"] != "2020 Baseline" and row["strategy_code"]
    }
    if "S0" in future_codes:
        return 0
    if "S5" in future_codes:
        return 1

    # The supplied dataset uses filenames ending in S0 for the baseline, so
    # zero-based strategy suffixes are the safest default for the flat layout.
    return 0


def discover_indoor_records(
    indoor_root,
    city_order: list[str],
    scenario_folders: dict[str, str],
    strategy_specs: list[dict],
):
    """Discover indoor CSV records in flat or nested layouts.

    Returns dictionaries containing ``path``, ``building_id``, ``city``,
    ``scenario_label``, ``strategy_label`` and ``relative_parent``.
    """
    root = Path(indoor_root)
    if not root.is_dir():
        return []

    candidates = []
    for path in root.rglob("*.csv"):
        if "_SET_Result" in path.name:
            continue
        if re.search(r"_dup\d*", path.name, flags=re.IGNORECASE):
            continue

        rel = path.relative_to(root)
        _, scenario_label = match_scenario(rel.parts[:-1], scenario_folders)
        if scenario_label is None:
            continue

        city = detect_city(rel, city_order)
        if city is None:
            continue

        building_id = extract_building_id(path)
        candidates.append(
            {
                "path": path,
                "relative_parent": rel.parent,
                "building_id": building_id,
                "city": city,
                "scenario_label": scenario_label,
                "strategy_code": extract_strategy_code(building_id),
                "path_strategy_label": _path_strategy_label(rel, strategy_specs),
            }
        )

    index_base = _infer_index_base(candidates)
    suffix_map = ZERO_BASED_STRATEGY_MAP if index_base == 0 else ONE_BASED_STRATEGY_MAP

    records = []
    for row in candidates:
        # The 2020 run is the baseline irrespective of its internal filename
        # suffix. For future files, an explicit legacy strategy directory takes
        # precedence, followed by the flat filename suffix.
        if row["scenario_label"] == "2020 Baseline":
            strategy_label = "S1 (Baseline_Evening27)"
        elif row["path_strategy_label"]:
            strategy_label = row["path_strategy_label"]
        else:
            strategy_label = suffix_map.get(row["strategy_code"])

        if strategy_label is None:
            print(
                f"  [WARN] Cannot infer strategy for {row['path']}; "
                f"suffix={row['strategy_code']!r}, index_base={index_base}. Skipped.",
                flush=True,
            )
            continue

        row = dict(row)
        row["strategy_label"] = strategy_label
        row["strategy_index_base"] = index_base
        records.append(row)

    city_rank = {city: idx for idx, city in enumerate(city_order)}
    records.sort(
        key=lambda r: (
            city_rank.get(r["city"], 999),
            r["scenario_label"],
            r["strategy_label"],
            r["building_id"],
        )
    )
    return records


def build_filename_index(root, filenames: Iterable[str]):
    """Index only requested companion filenames to avoid repeated full scans."""
    root = Path(root)
    wanted = set(filenames)
    index: dict[str, list[Path]] = {name: [] for name in wanted}
    if not root.is_dir() or not wanted:
        return index
    for path in root.rglob("*"):
        if path.is_file() and path.name in wanted:
            index[path.name].append(path)
    return index


def resolve_companion(
    companion_root,
    relative_parent: Path,
    filename: str,
    filename_index: dict[str, list[Path]],
    scenario_label: str,
    scenario_folders: dict[str, str],
):
    """Resolve Energy/Capacity file using same relative layout, then fallback."""
    root = Path(companion_root)
    direct = root / relative_parent / filename
    if direct.exists():
        return direct

    matches = filename_index.get(filename, [])
    if len(matches) == 1:
        return matches[0]

    scenario_matches = []
    for path in matches:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        _, candidate_label = match_scenario(rel.parts[:-1], scenario_folders)
        if candidate_label == scenario_label:
            scenario_matches.append(path)
    if len(scenario_matches) == 1:
        return scenario_matches[0]
    return direct

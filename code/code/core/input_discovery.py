"""Discover EnergyPlus result files across flat and legacy directory layouts.

Supported examples
------------------
Flat, year/scenario-first layout::

    IndoorEnv/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0.csv
    Energy/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0-meter.csv
    Capacity/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0-capacity.csv

Legacy nested layout::

    IndoorEnv/S1a_2020_Evening27/bei3jing1shi4/2020/<building>.csv

Records are returned in the order of ``city_order`` (see ``config/cities.py``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


def _suffix_maps(strategy_specs: list[dict]):
    """Filename suffix → strategy label, for zero-based (S0..) and one-based (S1..) naming.

    Derived from the shared STRATEGIES list in ``config.parameters`` so labels
    are defined in exactly one place.
    """
    labels = [spec["label"] for spec in strategy_specs]
    zero = {f"S{i}": label for i, label in enumerate(labels)}
    one = {f"S{i + 1}": label for i, label in enumerate(labels)}
    return zero, one


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
    for suffix in ("-meter.csv", "-capacity.csv", ".csv"):
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


def _drop_duplicate_baseline_runs(candidates: list[dict], index_base: int) -> list[dict]:
    """Keep one 2020 file per building.

    Every 2020 file is labelled as the baseline strategy. If a 2020 folder holds
    several strategy runs of the same building (e.g. ``..._S0`` and ``..._S1``),
    they would all be counted as baseline and double-count the building. Keep
    the baseline-suffix file (S0 / S1 by index base) and warn about the rest.
    """
    baseline_code = f"S{index_base}"
    groups: dict[tuple, list[dict]] = {}
    out = []
    for row in candidates:
        if row["scenario_label"] != "2020 Baseline":
            out.append(row)
            continue
        base = re.sub(r"_S\d+$", "", row["building_id"], flags=re.IGNORECASE)
        groups.setdefault((row["city"], base), []).append(row)

    for (_city, base), rows in groups.items():
        if len(rows) == 1:
            out.append(rows[0])
            continue
        rows = sorted(rows, key=lambda r: (r["strategy_code"] != baseline_code, r["building_id"]))
        keep, dropped = rows[0], rows[1:]
        print(
            f"  [WARN] 2020 Baseline has {len(rows)} runs of {base}; keeping "
            f"{keep['path'].name}, ignoring {[r['path'].name for r in dropped]}",
            flush=True,
        )
        out.append(keep)
    return out


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
    zero_map, one_map = _suffix_maps(strategy_specs)
    suffix_map = zero_map if index_base == 0 else one_map
    baseline_label = strategy_specs[0]["label"]
    candidates = _drop_duplicate_baseline_runs(candidates, index_base)

    records = []
    for row in candidates:
        # The 2020 run is the baseline irrespective of its internal filename
        # suffix. For future files, an explicit legacy strategy directory takes
        # precedence, followed by the flat filename suffix.
        if row["scenario_label"] == "2020 Baseline":
            strategy_label = baseline_label
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
    strategy_specs: list[dict] | None = None,
    city: str | None = None,
    city_order: list[str] | None = None,
):
    """Resolve the Energy/Capacity file belonging to one IndoorEnv record.

    1. Same relative folder as the IndoorEnv file (normal case).
    2. Otherwise search by filename, but only accept a candidate whose
       scenario, strategy folder and city all match the record, and only if
       exactly one such candidate exists. Anything else returns the (missing)
       direct path, so the caller reports the file as missing instead of
       silently pairing data from another scenario or strategy.
    """
    root = Path(companion_root)
    direct = root / relative_parent / filename
    if direct.exists():
        return direct

    want_strategy = (
        _path_strategy_label(Path(relative_parent) / filename, strategy_specs)
        if strategy_specs else None
    )
    accepted = []
    for path in filename_index.get(filename, []):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        _, candidate_label = match_scenario(rel.parts[:-1], scenario_folders)
        if candidate_label != scenario_label:
            continue
        if strategy_specs and _path_strategy_label(rel, strategy_specs) != want_strategy:
            continue
        if city and city_order and detect_city(rel, city_order) != city:
            continue
        accepted.append(path)
    if len(accepted) == 1:
        return accepted[0]
    return direct

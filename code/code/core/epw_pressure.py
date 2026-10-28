"""EPW atmospheric-pressure discovery, validation, and time alignment.

The original SET pipeline used city/scenario-specific EPW station pressure only
when converting humidity ratio to relative humidity. It did not pass pressure
to ``pythermalcomfort.models.set_tmp``.

EnergyPlus hourly output and EPW both use end-of-interval hour labels 1--24.
Hour 24 is preserved as 24 during matching; it is not converted to 0.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

_VALID_PRESSURE_MIN_PA = 50_000.0
_VALID_PRESSURE_MAX_PA = 110_000.0


def _norm(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value)).lower()


def _scenario_aliases(scenario_label: str, scenario_folder: str) -> list[str]:
    aliases = {
        scenario_label,
        scenario_folder,
        scenario_label.replace(" ", ""),
        scenario_folder.replace("-", "").replace(".", ""),
    }
    if scenario_label == "2020 Baseline":
        aliases.update({"2020", "2020baseline", "baseline2020"})
    else:
        year_match = re.search(r"(2040|2060)", scenario_label)
        pathway_match = re.search(r"(SSP1-2\.6|SSP2-4\.5|SSP5-8\.5)", scenario_label, re.IGNORECASE)
        if year_match and pathway_match:
            year = year_match.group(1)
            pathway = pathway_match.group(1).upper()
            aliases.update(
                {
                    f"{year}-{pathway}",
                    f"{year} {pathway}",
                    f"{year}{pathway}",
                }
            )
    return sorted({_norm(x) for x in aliases if _norm(x)}, key=len, reverse=True)


def _city_aliases(city: dict) -> list[str]:
    aliases = {
        city.get("pinyin", ""),
        city.get("en", ""),
        city.get("cn", ""),
        city.get("code", ""),
    }
    en = str(city.get("en", ""))
    if en:
        aliases.update({en + "shi", en + "city"})
    return sorted({_norm(x) for x in aliases if _norm(x)}, key=len, reverse=True)


def resolve_epw_file(
    epw_root: str | os.PathLike,
    city: dict,
    scenario_label: str,
    scenario_folder: str,
) -> Path:
    """Resolve exactly one city/scenario EPW under a recursive EPW root."""
    root = Path(epw_root)
    if not root.is_dir():
        raise FileNotFoundError(f"EPW root directory not found: {root}")

    files = sorted(root.rglob("*.epw"))
    if not files:
        raise FileNotFoundError(f"No .epw files found under: {root}")

    city_tokens = _city_aliases(city)
    scenario_tokens = _scenario_aliases(scenario_label, scenario_folder)
    candidates = []

    for path in files:
        rel_norm = _norm(str(path.relative_to(root)))
        city_hits = [token for token in city_tokens if token in rel_norm]
        scenario_hits = [token for token in scenario_tokens if token in rel_norm]
        if not city_hits or not scenario_hits:
            continue

        score = max(map(len, city_hits)) * 10 + max(map(len, scenario_hits))
        if _norm(scenario_folder) in rel_norm:
            score += 100
        candidates.append((score, path))

    if not candidates:
        raise FileNotFoundError(
            f"No EPW matched city={city.get('en')!r}, "
            f"scenario={scenario_label!r} under {root}."
        )

    best_score = max(score for score, _ in candidates)
    best = sorted(path for score, path in candidates if score == best_score)
    if len(best) != 1:
        paths = "\n  - ".join(str(path) for path in best)
        raise ValueError(
            f"Multiple EPW files matched city={city.get('en')!r}, "
            f"scenario={scenario_label!r}:\n  - {paths}"
        )
    return best[0]


# An EPW hourly record starts with: year, month, day, hour, minute, ...
_DATA_LINE = re.compile(r"^\s*-?\d{1,4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*,\s*-?\d{1,2}\s*,")
_EPW_MIN_FIELDS = 22   # up to field 22 (diffuse horizontal radiation) is all any step reads


def read_epw_table(epw_file: str | os.PathLike) -> pd.DataFrame:
    """Read the hourly records of an EPW file as text columns 0..N.

    Does not assume the header is exactly 8 lines, and does not treat quotes
    as special: the first line that looks like ``year,month,day,hour,minute,``
    starts the data, and every record is split on plain commas. This copes with
    files whose COMMENTS lines span several lines or contain stray ``"``.
    Columns are numbered as in the EPW spec minus one (1=month, 2=day, 3=hour,
    6=dry bulb, 7=dew point, 8=RH, 9=pressure, 13=GHI, 15=DHI).
    """
    path = Path(epw_file)
    if not path.is_file():
        raise FileNotFoundError(f"EPW file not found: {path}")
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "latin1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()

    start = next((i for i, line in enumerate(lines) if _DATA_LINE.match(line)), None)
    if start is None:
        head = " | ".join(line[:60] for line in lines[:10])
        raise ValueError(f"No hourly records found in EPW {path} (first lines: {head})")

    rows, bad = [], []
    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if not _DATA_LINE.match(line):
            bad.append(i + 1)
            continue
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < _EPW_MIN_FIELDS:
            bad.append(i + 1)
            continue
        rows.append(fields)
    if bad:
        raise ValueError(
            f"EPW {path}: {len(bad)} malformed hourly lines after the header "
            f"(line numbers, first 5: {bad[:5]})"
        )
    width = max(len(r) for r in rows)
    data = pd.DataFrame([r + [""] * (width - len(r)) for r in rows])
    data.attrs["header_lines"] = start
    return data


def epw_numeric(epw_file: str | os.PathLike, columns: dict[int, str]) -> pd.DataFrame:
    """``read_epw_table`` → selected columns, renamed and converted to numbers."""
    data = read_epw_table(epw_file)
    out = data[list(columns)].rename(columns=columns)
    return out.apply(pd.to_numeric, errors="coerce")


@lru_cache(maxsize=64)
def load_epw_pressure_grid(epw_file: str) -> np.ndarray:
    """Read EPW field 10 into ``grid[month, day, end_hour]``."""
    path = Path(epw_file)
    try:
        data = epw_numeric(path, {1: "month", 2: "day", 3: "hour", 9: "pressure_pa"})
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"Cannot read EPW file {path}: {exc}") from exc
    if len(data) not in (8760, 8784):
        raise ValueError(f"EPW {path} has {len(data)} hourly records (expected 8760 or 8784)")

    for column in data.columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    invalid = (
        data[["month", "day", "hour"]].isna().any(axis=1)
        | ~data["month"].between(1, 12)
        | ~data["day"].between(1, 31)
        | ~data["hour"].between(1, 24)
        | data["pressure_pa"].isna()
        | ~np.isfinite(data["pressure_pa"])
        | ~data["pressure_pa"].between(
            _VALID_PRESSURE_MIN_PA,
            _VALID_PRESSURE_MAX_PA,
        )
    )
    if invalid.any():
        examples = data.loc[invalid].head(5).to_dict("records")
        raise ValueError(
            f"Invalid EPW time/pressure values in {path}; "
            f"invalid rows={int(invalid.sum())}, examples={examples}"
        )

    data[["month", "day", "hour"]] = data[
        ["month", "day", "hour"]
    ].astype(int)

    duplicated = data.duplicated(["month", "day", "hour"], keep=False)
    if duplicated.any():
        examples = data.loc[duplicated].head(5).to_dict("records")
        raise ValueError(f"Duplicate EPW month/day/hour records in {path}: {examples}")

    grid = np.full((13, 32, 25), np.nan, dtype=float)
    grid[
        data["month"].to_numpy(),
        data["day"].to_numpy(),
        data["hour"].to_numpy(),
    ] = data["pressure_pa"].to_numpy(dtype=float)
    return grid


_EPLUS_DT_RE = (
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{1,2}):00:00"
)


def parse_eplus_datetime(energyplus_times: Sequence[str] | pd.Series):
    """Parse EnergyPlus hourly ``MM/DD HH:00:00`` labels into (month, day, hour) int arrays.

    Shared by the EPW alignment and the Step 1 night-hour filter so both read
    timestamps identically. Raises on any unparseable or out-of-range label.
    """
    text = pd.Series(energyplus_times, copy=False).astype(str).str.strip()
    parts = text.str.extract(_EPLUS_DT_RE)
    bad = parts.isna().any(axis=1)
    if bad.any():
        raise ValueError(
            f"Cannot parse EnergyPlus Date/Time values: {text[bad].head(5).tolist()}"
        )
    month = parts["month"].astype(int).to_numpy()
    day = parts["day"].astype(int).to_numpy()
    hour = parts["hour"].astype(int).to_numpy()
    out_of_range = (month < 1) | (month > 12) | (day < 1) | (day > 31) | (hour < 1) | (hour > 24)
    if out_of_range.any():
        raise ValueError(
            "EnergyPlus Date/Time outside valid bounds: "
            f"{text[out_of_range].head(5).tolist()}"
        )
    return month, day, hour


def parse_hours(energyplus_times: Sequence[str] | pd.Series) -> np.ndarray:
    """End-of-interval hour labels 1–24 (24 kept as 24)."""
    return parse_eplus_datetime(energyplus_times)[2]


def canonical_time_keys(energyplus_times: Sequence[str] | pd.Series) -> np.ndarray:
    """Normalised ``MM/DD  HH:00:00`` labels (EnergyPlus spacing), so files that
    write the same hour differently (``2:00:00`` vs ``02:00:00``) still match."""
    month, day, hour = parse_eplus_datetime(energyplus_times)
    return np.array([f"{m:02d}/{d:02d}  {h:02d}:00:00" for m, d, h in zip(month, day, hour)], dtype=object)


def align_epw_pressure(
    energyplus_times: Sequence[str] | pd.Series,
    pressure_grid: np.ndarray,
) -> np.ndarray:
    """Align EnergyPlus ``MM/DD HH:00:00`` rows to EPW pressure exactly."""
    month, day, hour = parse_eplus_datetime(energyplus_times)
    pressure = pressure_grid[month, day, hour]
    missing = ~np.isfinite(pressure)
    if missing.any():
        text = pd.Series(energyplus_times, copy=False).astype(str).str.strip()
        raise ValueError(
            f"EPW pressure missing for {int(missing.sum())} EnergyPlus rows; "
            f"examples={text[missing].head(5).tolist()}"
        )
    return pressure.astype(float, copy=False)


def pressure_for_times(
    energyplus_times: Sequence[str] | pd.Series,
    epw_file: str | os.PathLike,
) -> np.ndarray:
    grid = load_epw_pressure_grid(str(Path(epw_file).resolve()))
    return align_epw_pressure(energyplus_times, grid)

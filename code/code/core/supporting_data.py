"""Locate and read city supporting data (cluster map, population table).

Step 1 and Step 3 both use these helpers so they always resolve the same
files for the same city.

Accepted filenames (first match wins; .xlsx before .csv)::

    ClusterMap/cluster_{code}_{name}.xlsx|csv
    Population/{code}_{name}_full.xlsx|csv

where ``name`` may be an English name,
toned pinyin (bei3jing1shi4), or toneless pinyin (beijingshi).
"""

from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd


def _name_variants(city: dict) -> list[str]:
    en, cn, py = city["en"], city["label"], city["pinyin"]
    variants = [en, en.lower(), cn, cn, py, re.sub(r"\d", "", py)]
    seen, out = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def cluster_map_candidates(cluster_root, city: dict) -> list[str]:
    names = _name_variants(city)
    return [
        os.path.join(str(cluster_root), f"cluster_{city['code']}_{n}{ext}")
        for ext in (".xlsx", ".csv")
        for n in names
    ]


def population_candidates(pop_root, city: dict) -> list[str]:
    names = _name_variants(city)
    return [
        os.path.join(str(pop_root), f"{city['code']}_{n}_full{ext}")
        for ext in (".xlsx", ".csv")
        for n in names
    ]


def first_existing(paths):
    return next((p for p in paths if p and os.path.exists(p)), None)


def find_cluster_map(cluster_root, city: dict):
    return first_existing(cluster_map_candidates(cluster_root, city))


def find_population_table(pop_root, city: dict):
    return first_existing(population_candidates(pop_root, city))


def read_table(path):
    """Read a CSV (tolerant of Chinese encodings) or Excel table (first sheet)."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(path, engine="openpyxl")
    last_err = None
    for enc in ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin1"]:
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except Exception as exc:
            last_err = exc
    raise ValueError(f"Cannot read table {path}: {last_err}")


def residential_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep rows whose landUseTyp starts with 'Residential' (if the column exists)."""
    if "landUseTyp" not in df.columns:
        return df
    lu = df["landUseTyp"].astype(str).str.strip()
    return df[lu.str.startswith("Residential", na=False)].copy()


# Blank storey counts (Fnum == 0) are filled with the same rule the Population
# tables use (the Beijing reference build): Fnum = ceil(Height / 3). Rows whose
# height is unknown as well stay 0. Without this, every (LandNum, Cluster, Fnum)
# lookup misses those stock groups: both Step 1 (population per prototype) and
# Step 3 (how many real buildings a prototype stands for) key on Fnum, so the
# prototypes silently fell back to one represented building or unmatched population.
# Set MODEL_FILL_FNUM0=0 to restore the raw cluster-map values.
DEFAULT_STOREY_HEIGHT_M = 3.0


def fill_missing_fnum(df: pd.DataFrame, storey_height_m: float = DEFAULT_STOREY_HEIGHT_M) -> pd.DataFrame:
    """Replace Fnum == 0 with ceil(Height / storey_height_m) when a height exists."""
    if os.environ.get("MODEL_FILL_FNUM0", "1").strip().lower() in {"0", "false", "no", "off"}:
        return df
    if "Fnum" not in df.columns or "Height" not in df.columns:
        return df
    fnum = pd.to_numeric(df["Fnum"], errors="coerce").fillna(0)
    height = pd.to_numeric(df["Height"], errors="coerce").fillna(0).clip(lower=0)
    derived = np.ceil(height / float(storey_height_m)).astype(int)
    if not (fnum <= 0).any():
        return df
    df = df.copy()
    df["Fnum"] = np.where(fnum > 0, fnum, derived).astype(int)
    return df


def load_building_counts(cluster_root, city: dict) -> dict:
    """Return {(LandNum, Cluster, Fnum): number_of_residential_buildings}."""
    path = find_cluster_map(cluster_root, city)
    if path is None:
        print(
            f"  [WARN] cluster map not found for {city['en']}. "
            f"Tried: {cluster_map_candidates(cluster_root, city)}",
            flush=True,
        )
        return {}
    try:
        df = read_table(path)
    except Exception as exc:
        print(f"  [WARN] cannot read cluster map for {city['en']}: {exc}", flush=True)
        return {}

    df.columns = df.columns.astype(str).str.replace("﻿", "", regex=False).str.strip()
    df = residential_only(df)
    required = ["LandNum", "Cluster", "Fnum"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(
            f"  [WARN] cluster map missing columns {missing} for {city['en']}. "
            f"Available={list(df.columns)}",
            flush=True,
        )
        return {}
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df = fill_missing_fnum(df)
    return df.groupby(required).size().to_dict()

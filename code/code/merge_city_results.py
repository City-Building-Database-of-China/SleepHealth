"""
Merge a re-run of some cities into the existing full results.

Typical use (via run_all.py):
    python run_all.py --cities Beijing --merge

What happens
  1. The selected cities are computed into  output/_partial/<cities>/  (the main
     output is not touched while they run).
  2. This script replaces ONLY those cities' rows in the main summary tables
       output/thermal_comfort/floor_discomfort_hours.csv
       output/thermal_comfort/per_capita_discomfort_hours.csv
       output/cooling_system/prototype_cooling_capacity.csv
       output/cooling_system/city_coincident_cooling_peak.csv
     and replaces output/thermal_comfort/hourly_set/<city_english_lowercase>/ (hourly Excel archive).
     Rows of all other cities are kept unchanged.
  3. The previous versions of those files are copied to output/_backup/<time>/.
  4. Step 2 (pivot tables) is then re-run on the merged tables.

Standalone:  python code/merge_city_results.py <partial_output_dir> <city,city>
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True   # Avoid creating __pycache__ files.
import os
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config.paths import OUTPUT_ROOT
from config.cities import CITIES, to_english

TABLES = [
    "thermal_comfort/floor_discomfort_hours.csv",
    "thermal_comfort/per_capita_discomfort_hours.csv",
    "cooling_system/prototype_cooling_capacity.csv",
    "cooling_system/city_coincident_cooling_peak.csv",
]
CITY_RANK = {c["en"]: i for i, c in enumerate(CITIES)}


def merge(partial_root: Path, cities_en: list[str], main_root: Path = OUTPUT_ROOT) -> None:
    partial_root, main_root = Path(partial_root), Path(main_root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = main_root / "_backup" / f"{stamp}_before_merge_{'_'.join(cities_en).lower()}"
    wanted = set(cities_en)

    # Validate every input table before writing; validation errors leave the main tables unchanged.
    plans = []
    for rel in TABLES:
        main_f, part_f = main_root / rel, partial_root / rel
        if not main_f.exists():
            raise SystemExit(f"[ERROR] {main_f} not found — run the full pipeline once before merging.")
        if not part_f.exists():
            raise SystemExit(f"[ERROR] {part_f} not found — the partial run did not produce it.")
        main = pd.read_csv(main_f, encoding="utf-8-sig")
        part = pd.read_csv(part_f, encoding="utf-8-sig")
        main_city = main['City'].map(to_english)
        part_city = part['City'].map(to_english)
        unexpected = sorted(set(part_city.dropna()) - wanted)
        if unexpected:
            raise SystemExit(f"[ERROR] {part_f} contains other cities {unexpected}")
        missing = sorted(wanted - set(part_city.dropna()))
        if missing:
            raise SystemExit(f"[ERROR] {part_f} has no rows for {missing} — not merging.")
        plans.append((rel, main_f, main, main_city, part))

    backup.mkdir(parents=True, exist_ok=True)
    for rel, main_f, main, main_city, part in plans:
        (backup / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(main_f, backup / rel)
        kept = main[~main_city.isin(wanted)]
        merged = pd.concat([kept, part], ignore_index=True, sort=False)
        rank = merged['City'].map(to_english).map(CITY_RANK).fillna(999)
        merged = merged.iloc[rank.argsort(kind="stable")].reset_index(drop=True)
        # keep the main file's column order; new columns (e.g. more storeys) go at the end
        cols = list(main.columns) + [c for c in merged.columns if c not in main.columns]
        merged[cols].to_csv(main_f, index=False, encoding="utf-8-sig")
        print(f"   [MERGED] {rel}: replaced {int(main_city.isin(wanted).sum())} → {len(part)} rows "
              f"for {', '.join(cities_en)}; kept {len(kept)} rows of other cities")

    # Hourly Excel archive folders
    for c in CITIES:
        if c["en"] not in wanted:
            continue
        archive_dir = Path("thermal_comfort") / "hourly_set" / c["en"].lower()
        src = partial_root / archive_dir
        dst = main_root / archive_dir
        if src.is_dir():
            if dst.is_dir():
                backup_dst = backup / archive_dir
                backup_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(backup_dst))
            shutil.copytree(src, dst)
            print(f"   [MERGED] {archive_dir.as_posix()}/ replaced")
    print(f"   previous versions saved in {backup}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    merge(Path(sys.argv[1]), [to_english(x) for x in sys.argv[2].split(",")])

"""
Step 0: Input check (fast, read-only).

For every configured city it reports, per scenario:
  * IndoorEnv CSV files found, and how many have a matching Energy meter CSV
    and Capacity table
  * whether exactly one EPW resolves and can be read (needed by Step 1 for outdoor pressure)
and per city whether the cluster map and population table are found.

Exit code is 0 when at least one city has IndoorEnv data, 2 otherwise; any
gaps are printed as warnings so a long six-city run is not started blind.
"""
import sys
sys.dont_write_bytecode = True   # Avoid creating __pycache__ files.
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config.paths import (
    OUTPUT_ROOT, INDOOR_ROOT, ENERGY_ROOT, CAPACITY_ROOT, EPW_ROOT, CLUSTER_MAP_ROOT, POPULATION_ROOT,
)
from config.parameters import SCENARIO_ORDER, SCENARIOS, SCENARIO_FOLDERS, STRATEGIES
from config.cities import selected_cities
from core.input_discovery import discover_indoor_records, build_filename_index, resolve_companion
from core.epw_pressure import resolve_epw_file, load_epw_pressure_grid
from core.supporting_data import find_cluster_map, find_population_table
from core.city_matcher import parse_sheet_metadata
from core.run_report import add_problem, reset_problems, write_problems


def main():
    reset_problems(OUTPUT_ROOT, "step0")
    cities = selected_cities()
    print(f"\n{'='*72}\n  Step 0: Input check — {', '.join(c['en'] for c in cities)}\n{'='*72}")
    for name, root in [("IndoorEnv", INDOOR_ROOT), ("Energy", ENERGY_ROOT), ("Capacity", CAPACITY_ROOT),
                       ("EPW", EPW_ROOT), ("ClusterMap", CLUSTER_MAP_ROOT), ("Population", POPULATION_ROOT)]:
        print(f"   {name:<10} {root}  {'OK' if Path(root).is_dir() else 'MISSING'}")

    city_order = [c["pinyin"] for c in cities]
    records = discover_indoor_records(
        INDOOR_ROOT,
        city_order=city_order,
        scenario_folders=SCENARIO_FOLDERS,
        strategy_specs=STRATEGIES,
    )
    names_e = [f"{r['building_id']}-meter.csv" for r in records]
    names_c = [f"{r['building_id']}-capacity.csv" for r in records]
    idx_e = build_filename_index(ENERGY_ROOT, names_e)
    idx_c = build_filename_index(CAPACITY_ROOT, names_c)

    problems = []
    cities_with_data = 0
    for city in cities:
        recs = [r for r in records if r["city"] == city["pinyin"]]
        print(f"\n  ── {city['en']} ({city['label']}, {city['pinyin']}) ──")
        if recs:
            cities_with_data += 1
        cm = find_cluster_map(CLUSTER_MAP_ROOT, city)
        pp = find_population_table(POPULATION_ROOT, city)
        print(f"   cluster map : {os.path.basename(cm) if cm else 'MISSING'}")
        print(f"   population  : {os.path.basename(pp) if pp else 'MISSING'}")
        if not cm:
            problems.append(f"{city['en']}: cluster map missing (Step 1 per-capita + Step 3 building counts)")
        if not pp:
            problems.append(f"{city['en']}: population table missing (Step 1 per-capita)")

        shorts = [st["short"] for st in STRATEGIES]
        label_to_short = {st["label"]: st["short"] for st in STRATEGIES}
        # Every scenario/strategy is intended to cover the same city prototypes.
        # The union detects a missing IndoorEnv file even when its companions exist.
        prototype_ids = {
            re.sub(r"_S\d+$", "", r["building_id"], flags=re.IGNORECASE)
            for r in recs
        }
        print(f"   {'scenario':<16}" + "".join(f"{x:>5}" for x in shorts)
              + f"{'indoor':>8}{'energy':>8}{'capacity':>10}  EPW")
        for label in SCENARIO_ORDER:
            rs = [r for r in recs if r["scenario_label"] == label]
            n_e = n_c = 0
            for r in rs:
                bid = r["building_id"]
                if resolve_companion(ENERGY_ROOT, r["relative_parent"], f"{bid}-meter.csv", idx_e, label, SCENARIO_FOLDERS, STRATEGIES, r["city"], city_order).exists():
                    n_e += 1
                capacity_path = resolve_companion(CAPACITY_ROOT, r["relative_parent"], f"{bid}-capacity.csv", idx_c, label, SCENARIO_FOLDERS, STRATEGIES, r["city"], city_order)
                if capacity_path.exists():
                    n_c += 1
            try:
                epw = resolve_epw_file(EPW_ROOT, city=city, scenario_label=label, scenario_folder=SCENARIOS[label])
                epw_txt = Path(epw).name
                try:
                    load_epw_pressure_grid(str(Path(epw).resolve()))
                except Exception as exc:
                    epw_txt += "  [UNREADABLE]"
                    if rs:
                        problems.append(f"{city['en']} | {label}: EPW {Path(epw).name} cannot be read — "
                                        f"{str(exc).splitlines()[0]}")
            except Exception as exc:
                epw_txt = "MISSING" if isinstance(exc, FileNotFoundError) else "AMBIGUOUS"
                if rs:
                    problems.append(f"{city['en']} | {label}: EPW {epw_txt} — Step 1 will skip this scenario")
            per = {x: 0 for x in shorts}
            for r in rs:
                per[label_to_short[r["strategy_label"]]] += 1
            expected_shorts = shorts[:1] if label == SCENARIO_ORDER[0] else shorts
            for short in expected_shorts:
                present = {
                    re.sub(r"_S\d+$", "", r["building_id"], flags=re.IGNORECASE)
                    for r in rs if label_to_short[r["strategy_label"]] == short
                }
                missing = sorted(prototype_ids - present)
                if missing:
                    problems.append(
                        f"{city['en']} | {label} | {short}: "
                        f"{len(missing)}/{len(prototype_ids)} prototypes without IndoorEnv CSV "
                        f"(e.g. {missing[0]})"
                    )
            per_txt = "".join(f"{(per[x] if (rs and (label != SCENARIO_ORDER[0] or x == shorts[0])) else '-'):>5}" for x in shorts)
            print(f"   {label:<16}{per_txt}{len(rs):>8}{n_e:>8}{n_c:>10}  {epw_txt}")
            if rs and label != SCENARIO_ORDER[0]:
                lacking = [x for x in shorts if per[x] == 0]
                if lacking:
                    problems.append(f"{city['en']} | {label}: no files for strategy {', '.join(lacking)}")
            if recs and not rs:
                problems.append(f"{city['en']} | {label}: no IndoorEnv files for this scenario")
            if rs and n_e < len(rs):
                problems.append(f"{city['en']} | {label}: {len(rs) - n_e}/{len(rs)} buildings without Energy meter CSV")
            if rs and n_c < len(rs):
                problems.append(f"{city['en']} | {label}: {len(rs) - n_c}/{len(rs)} buildings without Capacity table")
        bad_ids = sorted({r["building_id"] for r in recs if parse_sheet_metadata(r["building_id"]) is None})
        if bad_ids:
            problems.append(
                f"{city['en']}: {len(bad_ids)} filenames without '_LandNum_Cluster_' "
                f"(e.g. {bad_ids[0]}) — excluded from per-capita and building counts"
            )
        if not recs:
            problems.append(f"{city['en']}: no IndoorEnv CSV files (filenames must start with '{city['pinyin']}_')")

    print(f"\n{'='*72}")
    if problems:
        print(f"  {len(problems)} input problem(s):")
        for p in problems:
            print(f"   [WARN] {p}")
            add_problem(item="input check", problem=p)
        write_problems(OUTPUT_ROOT, "step0")
    else:
        print("  All inputs present for every city and scenario.")
    print(f"{'='*72}")
    return 0 if cities_with_data else 2


if __name__ == "__main__":
    sys.exit(main())

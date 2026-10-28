"""
Step 3: Meter checks, installed capacity, and coincident peak load.

Input:  data/energyplus_outputs/IndoorEnv/
        data/energyplus_outputs/Energy/
        data/energyplus_outputs/Capacity/
        data/supporting_data/ClusterMap/

Output: output/cooling_system/prototype_cooling_capacity.csv
        output/cooling_system/city_coincident_cooling_peak.csv

Cities are defined in config/cities.py (MODEL_CITIES=... runs a subset).

Floor count
-----------
A prototype's floor count (matched to ``Fnum`` in the cluster map to get the
number of real buildings it represents) is taken from the IndoorEnv CSV, the
same way Step 1 does it. It is used for both the energy table and the peak
table, so the two always use the same building counts.
"""
import sys
sys.dont_write_bytecode = True   # Avoid creating __pycache__ files.
import os, sys, re, time, multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config.paths import (
    INDOOR_ROOT, ENERGY_ROOT, CAPACITY_ROOT, CLUSTER_MAP_ROOT, OUTPUT_ROOT,
)

BASE_INDOOR = str(INDOOR_ROOT)
BASE_ENERGY = str(ENERGY_ROOT)
BASE_CAPACITY = str(CAPACITY_ROOT)
BASE_CLUSTER = str(CLUSTER_MAP_ROOT)
OUTPUT_DIR = os.path.join(str(OUTPUT_ROOT), "cooling_system")

from config.parameters import MAX_WORKERS, NIGHT_HOURS, STRATEGIES, SCENARIO_FOLDERS, SCENARIO_ORDER
from config.cities import CITIES, selected_cities
from core.input_discovery import (
    build_filename_index,
    discover_indoor_records,
    resolve_companion,
)
from core.capacity_parser import parse_cooling_capacity
from core.city_matcher import parse_sheet_metadata, storey_numbers_from_columns
from core.supporting_data import load_building_counts
from core.epw_pressure import parse_hours, canonical_time_keys
from core.run_report import add_problem, reset_problems, write_problems, remove_stale

J_PER_KWH = 3_600_000


def _read_csv_robust(path):
    last_err = None
    for enc in ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin1"]:
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except Exception as e:
            last_err = e
    raise ValueError(f"Cannot read CSV: {path}; last error={last_err}")


def _meter_cooling_series(energy_path):
    """Read an EnergyPlus meter CSV.

    Returns (timestamps, Facility − Building [J], problem). ``problem`` is None
    when the file is usable; otherwise timestamps/values are None and the
    caller must treat energy and night load as *missing*, never as zero.
    """
    if not os.path.exists(energy_path):
        return None, None, "meter CSV not found"
    df_m = _read_csv_robust(energy_path)
    fc = [c for c in df_m.columns if "Electricity:Facility" in c]
    bc = [c for c in df_m.columns if "Electricity:Building" in c]
    if not fc or not bc:
        return None, None, "meter CSV lacks Electricity:Facility / Electricity:Building columns"
    fac = pd.to_numeric(df_m[fc[0]], errors='coerce').to_numpy(dtype=float)
    bld = pd.to_numeric(df_m[bc[0]], errors='coerce').to_numpy(dtype=float)
    bad = ~np.isfinite(fac) | ~np.isfinite(bld)
    if bad.any():
        return None, None, f"meter CSV has {int(bad.sum())} non-numeric/missing values"
    ts = pd.Series(canonical_time_keys(df_m.iloc[:, 0]))
    dup = ts.duplicated(keep=False)
    if dup.any():
        return None, None, f"meter CSV has {int(dup.sum())} duplicated timestamps (e.g. {ts[dup].iloc[0]!r})"
    return ts, fac - bld, None


def process_single_building(args):
    """Capacity/diagnostic row and night-time load series for one prototype.

    Returns (row_or_None, peak_series_or_None, error_or_None). Data problems
    that do not stop the row are listed in its data-issues field (see the root README.md).
    """
    bld_id, st_label, sc_label, city, indoor_path, energy_path, capacity_path, counts = args
    try:
        issues = []
        # ── indoor: schedules + floor count ──
        df_i = _read_csv_robust(str(indoor_path))
        floors = storey_numbers_from_columns(df_i.columns)
        n_floors = len(floors)

        # ── building count in the city stock ──
        meta = parse_sheet_metadata(bld_id)
        if meta and counts and n_floors:
            cnt = counts.get((meta[0], meta[1], n_floors))
        else:
            cnt = None
        count_matched = cnt is not None
        if cnt is None:
            cnt = 1   # Legacy fallback; the building-count match flag remains False.

        ts, cool_j, meter_problem = _meter_cooling_series(str(energy_path))
        if meter_problem:
            issues.append(meter_problem)

        # ── coincident night-time load (all night hours, as before) ──
        peak = None
        if meter_problem is None:
            hour = parse_hours(ts)
            night = np.isin(hour, NIGHT_HOURS)
            if night.any():
                peak = pd.Series(np.clip(cool_j[night], 0, None) * cnt, index=ts[night].to_numpy())

        cool_cols = [c for c in df_i.columns if "COOLING_PERIOD_SCHEDULE" in c]
        hvac_cols = [c for c in df_i.columns if "HVAC_CONDITIONEDTIME_SCHEDULE" in c]
        if not cool_cols or not hvac_cols:
            return None, peak, f"{bld_id}: indoor CSV lacks cooling/HVAC schedule columns"

        eff = ((df_i[cool_cols[0]] > 0) & (df_i[hvac_cols[0]] > 0)).to_numpy()
        eh = int(eff.sum())

        # ── cooling energy over effective cooling hours ──
        # NaN (not 0) whenever the meter is unusable or misses some of the hours.
        ck = np.nan
        if meter_problem is None:
            lookup = pd.Series(cool_j, index=ts.to_numpy())
            ta = canonical_time_keys(df_i.iloc[:, 0])[eff]
            vals = lookup.reindex(ta)
            n_missing = int(vals.isna().sum())
            if n_missing:
                issues.append(f"meter CSV lacks {n_missing}/{eh} effective cooling hours")
            else:
                ck = float(vals.sum()) / J_PER_KWH

        # ── installed capacity ──
        caps = parse_cooling_capacity(str(capacity_path))
        if not caps:
            issues.append("no cooling capacity found in -capacity.csv" if os.path.exists(str(capacity_path))
                          else "capacity CSV not found")

        row = {'PrototypeID': bld_id, 'Strategy': st_label, 'Scenario': sc_label, 'City': city,
               'Effective_Cooling_Hours': eh,
               'Represented_Buildings': cnt}
        for sn in sorted(caps):
            row[f'Storey_{sn}_Capacity_W'] = round(caps[sn], 2)
        # diagnostics (appended at the end of the table)
        row['Floor_Count'] = n_floors
        row['Building_Count_Matched'] = count_matched
        row['Capacity_Floor_Count'] = len(caps)
        row['Data_Issues'] = "; ".join(issues)
        return row, peak, None
    except Exception as e:
        return None, None, f"{bld_id}: {e}"


def build_task_list(cities):
    """Build tasks from year-first flat files or the legacy nested layout."""
    if not os.path.isdir(BASE_INDOOR):
        print(f"  [SKIP] INDOOR root not found: {BASE_INDOOR}", flush=True)
        return []

    city_order = [c["pinyin"] for c in cities]
    records = discover_indoor_records(
        BASE_INDOOR,
        city_order=city_order,
        scenario_folders=SCENARIO_FOLDERS,
        strategy_specs=STRATEGIES,
    )
    if not records:
        existing = sorted(
            [x for x in os.listdir(BASE_INDOOR) if os.path.isdir(os.path.join(BASE_INDOOR, x))]
        )[:20]
        print(f"  [INFO] No indoor CSV tasks found. BASE_INDOOR folders: {existing}", flush=True)
        return []

    energy_names = [f"{row['building_id']}-meter.csv" for row in records]
    capacity_names = [f"{row['building_id']}-capacity.csv" for row in records]
    energy_index = build_filename_index(BASE_ENERGY, energy_names)
    capacity_index = build_filename_index(BASE_CAPACITY, capacity_names)

    found = [c for c in cities if any(r["city"] == c["pinyin"] for r in records)]
    counts_by_city = {c["pinyin"]: load_building_counts(BASE_CLUSTER, c) for c in found}
    tasks = []
    for row in records:
        bid = row["building_id"]
        rel_parent = row["relative_parent"]
        energy_path = resolve_companion(
            BASE_ENERGY, rel_parent, f"{bid}-meter.csv",
            energy_index, row["scenario_label"], SCENARIO_FOLDERS,
            STRATEGIES, row["city"], city_order,
        )
        capacity_path = resolve_companion(
            BASE_CAPACITY, rel_parent, f"{bid}-capacity.csv",
            capacity_index, row["scenario_label"], SCENARIO_FOLDERS,
            STRATEGIES, row["city"], city_order,
        )
        tasks.append(
            (
                bid,
                row["strategy_label"],
                row["scenario_label"],
                row["city"],
                str(row["path"]),
                str(energy_path),
                str(capacity_path),
                counts_by_city.get(row["city"], {}),
            )
        )

    print(
        f"  >> Discovered {len(tasks)} tasks; cities={[c['en'] for c in found]}",
        flush=True,
    )
    missing = [c["en"] for c in cities if c not in found]
    if missing:
        print(f"  [WARN] No IndoorEnv files for: {', '.join(missing)}", flush=True)
    return tasks


def write_coincident_peak_table(peak_agg):
    """peak_agg: {(city, strategy, scenario): Series[timestamp → J summed over city]}."""
    rows = []
    for (city, st, sc), series in peak_agg.items():
        if series is None or series.empty:
            continue
        peak_timestamp = series.idxmax()
        rows.append(
            {
                'City': city,
                'Strategy': st,
                'Scenario': sc,
                'Night_Peak_kW': round(series.loc[peak_timestamp] / J_PER_KWH, 2),
                'Peak_Timestamp': peak_timestamp,
            }
        )
    if not rows:
        return None
    df_peak = pd.DataFrame(rows)
    df_peak = _sort_output(df_peak)
    output_path = PEAK_CSV
    df_peak.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f">> Peak table: {output_path} ({len(df_peak)} rows)")
    return df_peak


def _sort_output(df):
    """Stable city → scenario → strategy order (city order from config/cities.py)."""
    city_rank = {c["pinyin"]: i for i, c in enumerate(CITIES)}
    scen_rank = {s: i for i, s in enumerate(SCENARIO_ORDER)}
    strat_rank = {s["label"]: i for i, s in enumerate(STRATEGIES)}
    keys = pd.DataFrame({
        'c': df['City'].map(city_rank).fillna(999),
        's': df['Scenario'].map(scen_rank).fillna(999),
        't': df['Strategy'].map(strat_rank).fillna(999),
    })
    extra = ['PrototypeID'] if 'PrototypeID' in df.columns else []
    order = pd.concat([keys, df[extra]], axis=1).sort_values(['c', 's', 't'] + extra, kind='stable').index
    return df.loc[order].reset_index(drop=True)


ENERGY_CSV = os.path.join(OUTPUT_DIR, "prototype_cooling_capacity.csv")
PEAK_CSV = os.path.join(OUTPUT_DIR, "city_coincident_cooling_peak.csv")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    reset_problems(OUTPUT_ROOT, "step3")
    remove_stale(ENERGY_CSV, PEAK_CSV)
    cities = selected_cities()
    en_of = {c["pinyin"]: c["en"] for c in CITIES}
    print(f"\n{'='*60}")
    print(f"  Step 3: Energy + Capacity + Peak  ({len(cities)} cities)")
    print(f"{'='*60}")
    print(f"   Cities  : {', '.join(c['en'] for c in cities)}")
    print(f"   Indoor  : {BASE_INDOOR}  exists={os.path.isdir(BASE_INDOOR)}")
    print(f"   Energy  : {BASE_ENERGY}  exists={os.path.isdir(BASE_ENERGY)}")
    print(f"   Capacity: {BASE_CAPACITY}  exists={os.path.isdir(BASE_CAPACITY)}")

    print(">> Scanning tasks ...")
    tasks = build_task_list(cities)
    print(f"   {len(tasks)} building x strategy x scenario x city")
    if not tasks:
        print("[ERROR] Step 3 found no processing tasks. Check the input directory structure.")
        return 2

    nw = min(MAX_WORKERS, os.cpu_count() or 2)
    print(f">> {nw} workers ...")
    results, errors, peak_agg = [], [], {}
    n_tasks, n_peak = {}, {}
    done = 0; t0 = time.time()
    with ProcessPoolExecutor(max_workers=nw) as ex:
        fs = {ex.submit(process_single_building, t): t for t in tasks}
        for fu in as_completed(fs):
            done += 1
            row, peak, err = fu.result()
            task = fs[fu]
            key = (task[3], task[1], task[2])  # city, strategy, scenario
            n_tasks[key] = n_tasks.get(key, 0) + 1
            if err:
                errors.append(err)
                print(f"   [ERROR] {err}", flush=True)
                add_problem(en_of.get(task[3], task[3]), task[2], task[1], task[0], f"building skipped: {err}")
            if row is not None:
                results.append(row)
            if peak is not None:
                n_peak[key] = n_peak.get(key, 0) + 1
                prev = peak_agg.get(key)
                peak_agg[key] = peak if prev is None else prev.add(peak, fill_value=0)
            if done % 200 == 0 or done == len(tasks):
                print(f"   {done}/{len(tasks)}  {time.time()-t0:.0f}s", flush=True)

    if not results:
        write_problems(OUTPUT_ROOT, "step3")
        print("[ERROR] Step 3 completed the task scan but produced no valid results.")
        return 3

    df = pd.DataFrame(results)
    cap_cols = sorted([c for c in df.columns if c.startswith('Storey_') and 'Capacity' in c],
                      key=lambda x: int(re.search(r'\d+', x).group()))
    fixed = ['PrototypeID', 'Strategy', 'Scenario', 'City', 'Represented_Buildings', 'Effective_Cooling_Hours']
    all_cols = [c for c in fixed + cap_cols if c in df.columns]
    for c in df.columns:
        if c not in all_cols:
            all_cols.append(c)
    df = _sort_output(df[all_cols])
    op = ENERGY_CSV
    df.to_csv(op, index=False, encoding='utf-8-sig')
    print(f"\n>> {op}  ({len(df)} rows x {len(df.columns)} cols)")
    for c in cities:
        sub = df[df['City'] == c["pinyin"]]
        print(f"   {c['en']:<10} {len(sub):>6} records")

    write_coincident_peak_table(peak_agg)

    # ── problem log ──
    for _, r in df[df['Data_Issues'].astype(str).str.len() > 0].iterrows():
        add_problem(en_of.get(r['City'], r['City']), r['Scenario'], r['Strategy'], r['PrototypeID'], r['Data_Issues'])
    for key, n in sorted(n_tasks.items()):
        missing = n - n_peak.get(key, 0)
        if missing:
            city, st, sc = key
            add_problem(en_of.get(city, city), sc, st, "coincident peak",
                        f"peak excludes {missing}/{n} buildings (meter missing/invalid)")
    unmatched = df[~df['Building_Count_Matched'].astype(bool)]
    if len(unmatched):
        for city, grp in unmatched.groupby('City', sort=False):
            ids = sorted(set(grp['PrototypeID']))
            add_problem(en_of.get(city, city), "", "", "building count",
                        f"{len(ids)} prototype(s) match no (LandNum, Cluster, Fnum) group; represented building count set to 1: "
                        f"{', '.join(ids[:10])}{' ...' if len(ids) > 10 else ''}")
    # Two prototypes standing for the same stock group would both get its full
    # building count (double counting in the city-wide peak).
    meta = df['PrototypeID'].map(parse_sheet_metadata)
    keyed = df.assign(_k=[(m[0], m[1], f) if m else None for m, f in zip(meta, df['Floor_Count'])])
    keyed = keyed[keyed['_k'].notna() & keyed['Building_Count_Matched'].astype(bool)]
    for (city, st, sc, k), grp in keyed.groupby(['City', 'Strategy', 'Scenario', '_k'], sort=False):
        if len(grp) > 1:
            add_problem(en_of.get(city, city), sc, st, "building count",
                        f"{', '.join(grp['PrototypeID'])} share group (LandNum, Cluster, Fnum)={k}; "
                        "its buildings are counted once per prototype")
    cap_mismatch = df[(df['Capacity_Floor_Count'] > 0) & (df['Capacity_Floor_Count'] != df['Floor_Count'])]
    for _, r in cap_mismatch.iterrows():
        add_problem(en_of.get(r['City'], r['City']), r['Scenario'], r['Strategy'], r['PrototypeID'],
                    f"capacity storeys ({r['Capacity_Floor_Count']}) != IndoorEnv storeys ({r['Floor_Count']})")
    n_problems = write_problems(OUTPUT_ROOT, "step3")
    if n_problems:
        print(f"\n{'!'*60}\n  Step 3 finished with {n_problems} problem(s) — see run_problems/step3_problems.csv\n{'!'*60}")

    print(f"\n>> Step 3 done. ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

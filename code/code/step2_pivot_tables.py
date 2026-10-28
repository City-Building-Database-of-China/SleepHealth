"""
Step 2: Analysis-ready pivot tables for discomfort outputs.

Input:  output/thermal_comfort/floor_discomfort_hours.csv
        output/thermal_comfort/per_capita_discomfort_hours.csv

Output: output/pivot_tables/floor_discomfort_hours_pivot.xlsx
        output/pivot_tables/per_capita_discomfort_hours_pivot.xlsx
"""
import sys
sys.dont_write_bytecode = True   # Avoid creating __pycache__ files.
import os
import sys
from collections import defaultdict
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from config.paths import OUTPUT_ROOT
from config.parameters import SCENARIO_ORDER, STRATEGY_LABELS, STRATEGY_SHORT, BASELINE_SCENARIO
from config.cities import CITY_ORDER_EN, to_english
from core.run_report import remove_stale

SET_DIR = os.path.join(str(OUTPUT_ROOT), "thermal_comfort")
PER_CAPITA_DIR = os.path.join(str(OUTPUT_ROOT), "thermal_comfort")
PIVOT_DIR = os.path.join(str(OUTPUT_ROOT), "pivot_tables")

STRATEGIES = list(STRATEGY_LABELS)
STRAT_SHORT = list(STRATEGY_SHORT)
SCENARIOS = list(SCENARIO_ORDER)
CITY_RANK = {en: i for i, en in enumerate(CITY_ORDER_EN)}


def _map_city(series):
    """Map pinyin / English / Chinese city names to English; fail loudly on unknowns."""
    mapped = series.map(lambda x: to_english(x))
    unknown = sorted(set(series[mapped.isna()].astype(str)))
    if unknown:
        raise ValueError(f"Unknown city names in input: {unknown}. Add them to config/cities.py")
    return mapped


def _floor_key(floor):
    """STOREY_12 → 12; anything else sorts after numbered floors."""
    parts = str(floor).split('_')
    if len(parts) >= 2 and parts[1].isdigit():
        return (0, int(parts[1]), '')
    return (1, 0, str(floor))


def build_pivot_a():
    """Per-floor discomfort hours pivot, one row per (city,building,floor)."""
    path = os.path.join(SET_DIR, "floor_discomfort_hours.csv")
    if not os.path.exists(path):
        print(f"   [ERROR] Not found: {path}")
        return None
    df = pd.read_csv(path, encoding='utf-8-sig')
    df['city_en'] = _map_city(df['City'])

    data = defaultdict(lambda: {'night_hours': None})
    for _, row in df.iterrows():
        key = (row['city_en'], row['PrototypeID'], row['Floor'])
        data[key][(row['Scenario'], row['Strategy'])] = row['Discomfort_Hours']
        if data[key]['night_hours'] is None:
            data[key]['night_hours'] = row['Night_Total_Hours']
    if not data: return None

    # Backfill Baseline S2-S5 from S1
    s1_label = STRATEGIES[0]
    for vals in data.values():
        base_val = vals.get((BASELINE_SCENARIO, s1_label))
        if base_val is not None:
            for st in STRATEGIES[1:]:
                vals.setdefault((BASELINE_SCENARIO, st), base_val)

    # Header rows
    h1 = ['','','','']
    h2 = ['City','BuildingID','Floor','Night_Total_Hours']
    for sc in SCENARIOS:
        if sc == BASELINE_SCENARIO:
            h1.extend([sc]); h2.extend(['Baseline'])
        else:
            h1.extend([sc]+['']*(len(STRAT_SHORT)-1))
            h2.extend(STRAT_SHORT)

    rows = [h1, h2]
    sorted_keys = sorted(data.keys(), key=lambda x: (CITY_RANK.get(x[0], 999), x[1], _floor_key(x[2])))
    cur_city = cur_bld = None

    for city, bid, floor in sorted_keys:
        rd = data[(city, bid, floor)]
        row = [city if cur_city!=city else '', bid if cur_bld!=bid else '',
               floor, rd.get('night_hours','')]
        cur_city = city; cur_bld = bid
        for sc in SCENARIOS:
            if sc == BASELINE_SCENARIO:
                v = rd.get((sc, STRATEGIES[0]), '')
                row.append(v if v!='' else '')
            else:
                for st in STRATEGIES:
                    v = rd.get((sc, st), '')
                    row.append(v if v!='' else '')
        rows.append(row)
    return pd.DataFrame(rows)


def build_pivot_b():
    """Per-capita discomfort hours pivot, one row per (city-scenario)."""
    path = os.path.join(PER_CAPITA_DIR, "per_capita_discomfort_hours.csv")
    if not os.path.exists(path):
        print(f"   [ERROR] Not found: {path}")
        return None
    df = pd.read_csv(path, encoding="utf-8-sig")

    # Backfill 2020 Baseline for S2-S5 from S1
    s1_label = STRATEGIES[0]
    for city in df['City'].unique():
        base_mask = (df['City']==city) & (df['Scenario']==BASELINE_SCENARIO)
        s1_rows = df[base_mask & (df['Strategy']==s1_label)]
        if s1_rows.empty: continue
        s1_val = s1_rows['Hours_Per_Resident'].values[0]
        for st in STRATEGIES[1:]:
            if df[base_mask & (df['Strategy']==st)].empty:
                new_row = {'City':city,'Scenario':BASELINE_SCENARIO,'Strategy':st,
                           'Hours_Per_Resident':s1_val,
                           'Total_Person_Hours':s1_rows['Total_Person_Hours'].values[0],
                           'Total_Population':s1_rows['Total_Population'].values[0],
                           'Temperature_Basis':s1_rows['Temperature_Basis'].values[0]}
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    df['city_en'] = _map_city(df['City'])
    df['row_label'] = df['city_en'] + ' - ' + df['Scenario']
    pivot = df.pivot_table(index='row_label', columns='Strategy', values='Hours_Per_Resident', aggfunc='first')
    ordered = [s for s in STRATEGIES if s in pivot.columns]
    pivot = pivot[ordered].round(1)
    # Rows: configured city order, then scenario order.
    scen_rank = {sc: i for i, sc in enumerate(SCENARIOS)}
    def _row_key(label):
        city, _, sc = label.partition(' - ')
        return (CITY_RANK.get(city, 999), scen_rank.get(sc, 999), label)
    pivot = pivot.loc[sorted(pivot.index, key=_row_key)]
    pivot = pivot.rename(columns=dict(zip(STRATEGIES, STRAT_SHORT)))
    return pivot.reset_index()


def main():
    os.makedirs(PIVOT_DIR, exist_ok=True)
    path_a = os.path.join(PIVOT_DIR, "floor_discomfort_hours_pivot.xlsx")
    path_b = os.path.join(PIVOT_DIR, "per_capita_discomfort_hours_pivot.xlsx")
    remove_stale(path_a, path_b)
    print(f"\n{'='*60}")
    print("  Step 2: Pivot Tables")
    print(f"{'='*60}")

    ta = build_pivot_a()
    if ta is not None:
        ta.to_excel(path_a, index=False, header=False)
        print(f"   [OK] {path_a}  ({ta.shape})")

    tb = build_pivot_b()
    if tb is not None:
        tb.to_excel(path_b, index=False)
        print(f"   [OK] {path_b}  ({tb.shape})")

    if ta is None or tb is None:
        print("[ERROR] Step 2 could not build all required pivot tables.")
        return 2

    print(">> Step 2 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

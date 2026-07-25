"""
Step 2: Analysis-ready pivot tables for discomfort outputs.

Input:  output/set_calculations/summary_uncomfortable_hours.csv
        output/per_capita_hours/per_capita_hours_summary.csv

Output: output/pivot_tables/total_uncomfortable_hours_pivot.xlsx
        output/pivot_tables/per_capita_hours_pivot.xlsx
"""
import os
from collections import defaultdict
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from config.paths import OUTPUT_ROOT
from config.parameters import SCENARIO_ORDER

SET_DIR = os.path.join(str(OUTPUT_ROOT), "set_calculations")
PER_CAPITA_DIR = os.path.join(str(OUTPUT_ROOT), "per_capita_hours")
PIVOT_DIR = os.path.join(str(OUTPUT_ROOT), "pivot_tables")

STRATEGIES = [
    "S1 (Baseline_Evening27)", "S2 (FixedCap_AllDay_32_27)",
    "S3 (FixedCap_Evening26)", "S4 (FixedCap_AllDay_32_26)",
    "S5 (AutoSize_AllDay_32_26)",
]
STRAT_SHORT = ["S1","S2","S3","S4","S5"]
BASELINE_SCENARIO = "2020 Baseline"
SCENARIOS = list(SCENARIO_ORDER)

PINYIN_TO_EN = {
    'bei3jing1shi4':'Beijing','guang3zhou1shi4':'Guangzhou',
    'shang4hai3shi4':'Shanghai','shen1zhen4shi4':'Shenzhen',
    'wu3han4shi4':'Wuhan','xia4men2shi4':'Xiamen',
    # also direct Chinese->English (for per-capita CSV)
    'Beijing':'Beijing','Guangzhou':'Guangzhou','Shanghai':'Shanghai',
    'Shenzhen':'Shenzhen','Wuhan':'Wuhan','Xiamen':'Xiamen',
    '北京市':'Beijing','上海市':'Shanghai','广州市':'Guangzhou',
    '深圳市':'Shenzhen','武汉市':'Wuhan','厦门市':'Xiamen',
}


def build_pivot_a():
    """Per-floor discomfort hours pivot, one row per (city,building,floor)."""
    path = os.path.join(SET_DIR, "summary_uncomfortable_hours.csv")
    if not os.path.exists(path):
        print(f"   [ERROR] Not found: {path}")
        return None
    df = pd.read_csv(path, encoding='utf-8-sig')
    df['city_en'] = df['城市'].map(PINYIN_TO_EN)

    data = defaultdict(lambda: {'night_hours': None})
    for _, row in df.iterrows():
        key = (row['city_en'], row['建筑ID'], row['楼层'])
        data[key][(row['情景'], row['策略'])] = row['不舒适小时数']
        if data[key]['night_hours'] is None:
            data[key]['night_hours'] = row['夜间总时数']
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
    sorted_keys = sorted(data.keys(), key=lambda x: (x[0], x[1],
        int(x[2].split('_')[1]) if '_' in str(x[2]) else 0))
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
    path = os.path.join(PER_CAPITA_DIR, "per_capita_hours_summary.csv")
    if not os.path.exists(path):
        print(f"   [ERROR] Not found: {path}")
        return None
    df = pd.read_csv(path, encoding="utf-8-sig")

    # Backfill 2020 Baseline for S2-S5 from S1
    s1_label = STRATEGIES[0]
    for city in df['城市'].unique():
        base_mask = (df['城市']==city) & (df['情景']==BASELINE_SCENARIO)
        s1_rows = df[base_mask & (df['策略']==s1_label)]
        if s1_rows.empty: continue
        s1_val = s1_rows['Hours_Per_Resident'].values[0]
        for st in STRATEGIES[1:]:
            if df[base_mask & (df['策略']==st)].empty:
                new_row = {'城市':city,'情景':BASELINE_SCENARIO,'策略':st,
                           'Hours_Per_Resident':s1_val,
                           'Total_Person_Hours':s1_rows['Total_Person_Hours'].values[0],
                           'Total_Population':s1_rows['Total_Population'].values[0],
                           '温度基准':s1_rows['温度基准'].values[0]}
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    df['city_en'] = df['城市'].map(PINYIN_TO_EN)
    df['row_label'] = df['city_en'] + ' - ' + df['情景']
    pivot = df.pivot_table(index='row_label', columns='策略', values='Hours_Per_Resident', aggfunc='first')
    ordered = [s for s in STRATEGIES if s in pivot.columns]
    pivot = pivot[ordered].round(1)
    pivot = pivot.rename(columns=dict(zip(STRATEGIES, STRAT_SHORT)))
    return pivot.reset_index()


def main():
    os.makedirs(PIVOT_DIR, exist_ok=True)
    print(f"\n{'='*60}")
    print("  Step 2: Pivot Tables")
    print(f"{'='*60}")

    ta = build_pivot_a()
    if ta is not None:
        path_a = os.path.join(PIVOT_DIR, "total_uncomfortable_hours_pivot.xlsx")
        ta.to_excel(path_a, index=False, header=False)
        print(f"   [OK] {path_a}  ({ta.shape})")

    tb = build_pivot_b()
    if tb is not None:
        path_b = os.path.join(PIVOT_DIR, "per_capita_hours_pivot.xlsx")
        tb.to_excel(path_b, index=False)
        print(f"   [OK] {path_b}  ({tb.shape})")

    if ta is None or tb is None:
        print("[ERROR] Step 2 could not build all required pivot tables.")
        return 2

    print(">> Step 2 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

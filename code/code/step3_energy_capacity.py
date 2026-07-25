"""
Step 3: Cooling energy, installed capacity, and coincident peak load.

Input:  data/energyplus_outputs/IndoorEnv/
        data/energyplus_outputs/Energy/
        data/energyplus_outputs/Capacity/
        data/supporting_data/ClusterMap/

Output: output/energy_capacity/energy_capacity_summary.csv
        output/energy_capacity/coincident_peak_load.csv
"""
import os, sys, re, glob, time, multiprocessing
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
OUTPUT_DIR = os.path.join(str(OUTPUT_ROOT), "energy_capacity")

from config.parameters import MAX_WORKERS, NIGHT_HOURS, SCENARIOS
from core.input_discovery import (
    build_filename_index,
    discover_indoor_records,
    resolve_companion,
)

CITY_LIST = ["bei3jing1shi4"]

CITY_CODE_CN = {
    "bei3jing1shi4":("110000","北京市"), "shang4hai3shi4":("310000","上海市"),
    "guang3zhou1shi4":("440100","广州市"), "shen1zhen4shi4":("440300","深圳市"),
    "wu3han4shi4":("420100","武汉市"), "xia4men2shi4":("350200","厦门市"),
}

STRATEGIES = [
    {"label":"S1 (Baseline_Evening27)",  "d2020":"S1a_2020_Evening27",         "dfuture":"S1b_Future_FixedCap_Evening27"},
    {"label":"S2 (FixedCap_AllDay_32_27)","d2020":None,                          "dfuture":"S2_Future_FixedCap_AllDay_32_27"},
    {"label":"S3 (FixedCap_Evening26)",   "d2020":None,                          "dfuture":"S3_Future_FixedCap_Evening26"},
    {"label":"S4 (FixedCap_AllDay_32_26)","d2020":None,                          "dfuture":"S4_Future_FixedCap_AllDay_32_26"},
    {"label":"S5 (AutoSize_AllDay_32_26)","d2020":None,                          "dfuture":"S5_Future_AutoSize_AllDay_32_26"},
]

SCENARIO_FOLDERS = {folder: label for label, folder in SCENARIOS.items()}


def _read_csv_robust(path):
    last_err = None
    for enc in ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin1"]:
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except Exception as e:
            last_err = e
    raise ValueError(f"Cannot read CSV: {path}; last error={last_err}")


def _read_supporting_table(path):
    """Read a supporting-data table in CSV or Excel format."""
    ext = os.path.splitext(path)[1].lower()
    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(path, engine="openpyxl")
    return _read_csv_robust(path)


def _norm_scenario_text(x):
    return re.sub(r"[^0-9a-zA-Z]+", "", str(x).lower())


def _load_building_counts(pinyin):
    code, chn = CITY_CODE_CN[pinyin]
    city_en = {
        "bei3jing1shi4": "Beijing",
        "shang4hai3shi4": "Shanghai",
        "guang3zhou1shi4": "Guangzhou",
        "shen1zhen4shi4": "Shenzhen",
        "wu3han4shi4": "Wuhan",
        "xia4men2shi4": "Xiamen",
    }[pinyin]

    candidates = [
        os.path.join(BASE_CLUSTER, f"cluster_{code}_{city_en}.xlsx"),
        os.path.join(BASE_CLUSTER, f"cluster_{code}_{chn}.xlsx"),
        os.path.join(BASE_CLUSTER, f"cluster_{code}_{city_en}.csv"),
        os.path.join(BASE_CLUSTER, f"cluster_{code}_{chn}.csv"),
    ]
    cf = next((path for path in candidates if os.path.exists(path)), None)
    if cf is None:
        print(
            f"  [WARN] cluster map not found for {pinyin}. Tried: {candidates}",
            flush=True,
        )
        return {}

    try:
        df = _read_supporting_table(cf)
    except Exception as e:
        print(f"  [WARN] cannot read cluster map for {pinyin}: {e}", flush=True)
        return {}

    df.columns = df.columns.astype(str).str.replace("\ufeff", "", regex=False).str.strip()
    if 'landUseTyp' in df.columns:
        df['landUseTyp'] = df['landUseTyp'].astype(str).str.strip()
        df = df[df['landUseTyp'].str.startswith('Residential', na=False)]

    required = ['LandNum','Cluster','Fnum']
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"  [WARN] cluster map missing columns {missing} for {pinyin}. Available={list(df.columns)}", flush=True)
        return {}

    for col in required:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
    return df.groupby(['LandNum','Cluster','Fnum']).size().to_dict()


def _match_scenario(dir_name):
    key = _norm_scenario_text(dir_name)
    if key in {"2020", "2020baseline"}:
        return "2020 Baseline"
    for k, v in SCENARIO_FOLDERS.items():
        if _norm_scenario_text(k) in key or _norm_scenario_text(v) in key:
            return v
    return None


def process_single_building(args):
    bld_id, st_label, sc_label, city, indoor_path, energy_path, capacity_path, counts = args
    try:
        ip = str(indoor_path)
        if not os.path.exists(ip): return None
        df_i = _read_csv_robust(ip)
        cool_cols = [c for c in df_i.columns if "COOLING_PERIOD_SCHEDULE" in c]
        hvac_cols = [c for c in df_i.columns if "HVAC_CONDITIONEDTIME_SCHEDULE" in c]
        if not cool_cols or not hvac_cols: return None
        eff = (df_i[cool_cols[0]]>0)&(df_i[hvac_cols[0]]>0)
        eh = int(eff.sum())
        ta = df_i.iloc[:,0].astype(str).str.strip()

        # cooling energy
        mp = str(energy_path)
        ck = 0.0
        if os.path.exists(mp) and eh>0:
            df_m = _read_csv_robust(mp)
            fc = [c for c in df_m.columns if "Electricity:Facility" in c]
            bc = [c for c in df_m.columns if "Electricity:Building" in c]
            if fc and bc:
                fac = pd.to_numeric(df_m[fc[0]],errors='coerce').fillna(0)
                bld = pd.to_numeric(df_m[bc[0]],errors='coerce').fillna(0)
                mt = {t.strip():i for i,t in enumerate(df_m.iloc[:,0].astype(str).str.strip())}
                tj = sum(fac[mt[t]]-bld[mt[t]] for i,t in enumerate(ta) if eff[i] and t.strip() in mt)
                ck = tj / 3_600_000

        # capacity
        cp = str(capacity_path)
        caps = {}
        if os.path.exists(cp):
            content = ""
            for enc in ['utf-8','gbk']:
                try:
                    with open(cp, encoding=enc) as f: content = f.read()
                    break
                except: pass
            if content:
                cs = content.find("Coil:Cooling:DX:SingleSpeed")
                if cs>-1:
                    nc = content.find("<b>Coil:", cs+30)
                    section = content[cs:(nc if nc>0 else cs+3000)]
                    rows_html = re.findall(r'<tr>(.*?)</tr>', section, re.DOTALL)
                    us_i = ds_i = None
                    for rh in rows_html:
                        cells = re.findall(r'<td[^>]*>(.*?)</td>', rh, re.DOTALL)
                        cells_c = [re.sub(r'<[^>]+>','',c).strip() for c in cells]
                        if not cells_c: continue
                        joined = ' '.join(cells_c)
                        if 'Total Cooling Capacity' in joined:
                            for j, c in enumerate(cells_c):
                                if 'User-Specified' in c and 'Gross Rated Total Cooling Capacity' in c: us_i=j
                                elif 'Design Size' in c and 'Gross Rated Total Cooling Capacity' in c: ds_i=j
                            continue
                        if us_i is not None or ds_i is not None:
                            nm = cells_c[0]
                            sm = re.search(r'STOREY\s*(\d+)', nm, re.IGNORECASE)
                            if sm and "COOLING COIL" in nm.upper():
                                sn = int(sm.group(1)); cv = 0.0
                                for idx in [us_i, ds_i]:
                                    if idx is not None and idx<len(cells_c):
                                        try:
                                            v = float(cells_c[idx])
                                            if v>0: cv=v; break
                                        except: pass
                                if cv>0: caps[sn]=cv

        # building count
        m = re.search(r'_(\d+)_(\d+)_', bld_id)
        if m and counts:
            ln, cl = int(m.group(1)), int(m.group(2))
            fn = len(caps) if caps else 1
            cnt = counts.get((ln,cl,fn), 1)
        else: cnt = 1

        row = {'建筑ID':bld_id,'策略':st_label,'情景':sc_label,'城市':city,
               '有效制冷小时数':eh,'纯制冷能耗_kWh':round(ck,4),
               '实际栋数':cnt,'全市纯制冷能耗_kWh':round(ck*cnt,4)}
        for sn in sorted(caps): row[f'Storey_{sn}_Capacity_W'] = round(caps[sn],2)
        return row
    except Exception as e:
        print(f"   [ERROR] {bld_id}: {e}")
        return None


def build_task_list():
    """Build tasks from year-first flat files or the legacy nested layout."""
    if not os.path.isdir(BASE_INDOOR):
        print(f"  [SKIP] INDOOR root not found: {BASE_INDOOR}", flush=True)
        return []

    records = discover_indoor_records(
        BASE_INDOOR,
        city_order=CITY_LIST,
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
    capacity_names = [f"{row['building_id']}-table.htm" for row in records]
    capacity_names += [f"{row['building_id']}-table.html" for row in records]
    energy_index = build_filename_index(BASE_ENERGY, energy_names)
    capacity_index = build_filename_index(BASE_CAPACITY, capacity_names)

    counts_by_city = {city: _load_building_counts(city) for city in CITY_LIST}
    tasks = []
    for row in records:
        bid = row["building_id"]
        rel_parent = row["relative_parent"]
        energy_path = resolve_companion(
            BASE_ENERGY,
            rel_parent,
            f"{bid}-meter.csv",
            energy_index,
            row["scenario_label"],
            SCENARIO_FOLDERS,
        )
        capacity_path = resolve_companion(
            BASE_CAPACITY,
            rel_parent,
            f"{bid}-table.htm",
            capacity_index,
            row["scenario_label"],
            SCENARIO_FOLDERS,
        )
        if not capacity_path.exists():
            capacity_path = resolve_companion(
                BASE_CAPACITY,
                rel_parent,
                f"{bid}-table.html",
                capacity_index,
                row["scenario_label"],
                SCENARIO_FOLDERS,
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

    found_cities = [city for city in CITY_LIST if any(t[3] == city for t in tasks)]
    print(
        f"  >> Discovered {len(tasks)} tasks; Beijing is processed first when present; "
        f"cities={found_cities}",
        flush=True,
    )
    return tasks

def _capacity_floor_count(capacity_path):
    if not capacity_path or not os.path.exists(capacity_path):
        return 1
    content = ""
    for enc in ['utf-8', 'gb18030', 'gbk', 'latin1']:
        try:
            with open(capacity_path, encoding=enc) as f:
                content = f.read()
            break
        except Exception:
            continue
    if not content:
        return 1
    floors = set(
        re.findall(
            r'STOREY\s*(\d+)\s+PTAC\s+COOLING\s+COIL',
            content,
            re.IGNORECASE,
        )
    )
    return len(floors) if floors else 1


def build_coincident_peak_table(tasks):
    """Aggregate night-time city loads using the same discovered files as Step 3."""
    print("\n>> Coincident peak ...")
    grouped = {}
    for task in tasks:
        bid, st, sc, city, _ip, energy_path, capacity_path, counts = task
        grouped.setdefault((city, st, sc), []).append(
            (bid, energy_path, capacity_path, counts)
        )

    rows = []
    night_set = set(NIGHT_HOURS)
    for (city, st, sc), records in grouped.items():
        agg = {}
        for bid, energy_path, capacity_path, counts in records:
            if not os.path.exists(energy_path):
                continue

            match = re.search(r'_(\d+)_(\d+)_', bid)
            floor_count = _capacity_floor_count(capacity_path)
            count = (
                counts.get((int(match.group(1)), int(match.group(2)), floor_count), 1)
                if match and counts
                else 1
            )

            df = _read_csv_robust(energy_path)
            facility_cols = [c for c in df.columns if "Electricity:Facility" in c]
            building_cols = [c for c in df.columns if "Electricity:Building" in c]
            if not facility_cols or not building_cols:
                continue

            facility = pd.to_numeric(df[facility_cols[0]], errors='coerce').fillna(0)
            building = pd.to_numeric(df[building_cols[0]], errors='coerce').fillna(0)
            timestamps = df.iloc[:, 0].astype(str).str.strip()
            for timestamp, facility_j, building_j in zip(timestamps, facility, building):
                hour_match = re.search(r'(\d{2}):00:00', timestamp)
                if hour_match and int(hour_match.group(1)) in night_set:
                    cooling_j = max(facility_j - building_j, 0)
                    agg[timestamp] = agg.get(timestamp, 0) + cooling_j * count

        if agg:
            peak_timestamp = max(agg, key=agg.get)
            rows.append(
                {
                    '城市': city,
                    '策略': st,
                    '情景': sc,
                    '夜间峰值_kW': round(agg[peak_timestamp] / 3_600_000, 2),
                    '峰值时刻': peak_timestamp,
                }
            )

    if rows:
        df_peak = pd.DataFrame(rows)
        output_path = os.path.join(OUTPUT_DIR, "coincident_peak_load.csv")
        df_peak.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f">> Peak table: {output_path} ({len(df_peak)} rows)")
        return df_peak
    return None

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"\n{'='*60}")
    print("  Step 3: Energy + Capacity + Peak")
    print(f"{'='*60}")
    print(f"   Indoor  : {BASE_INDOOR}  exists={os.path.isdir(BASE_INDOOR)}")
    print(f"   Energy  : {BASE_ENERGY}  exists={os.path.isdir(BASE_ENERGY)}")
    print(f"   Capacity: {BASE_CAPACITY}  exists={os.path.isdir(BASE_CAPACITY)}")

    print(">> Scanning tasks ...")
    tasks = build_task_list()
    print(f"   {len(tasks)} building x strategy x scenario x city")
    if not tasks:
        print("[ERROR] Step 3 found no processing tasks. Check the input directory structure.")
        return 2

    nw = min(MAX_WORKERS, os.cpu_count() or 2)
    print(f">> {nw} workers ...")
    results = []; done = 0; t0 = time.time()
    with ProcessPoolExecutor(max_workers=nw) as ex:
        fs = {ex.submit(process_single_building, t): t for t in tasks}
        for fu in as_completed(fs):
            done += 1; row = fu.result()
            if row is not None: results.append(row)
            if done % 200 == 0 or done == len(tasks):
                print(f"   {done}/{len(tasks)}  {time.time()-t0:.0f}s")

    if results:
        df = pd.DataFrame(results)
        cap_cols = sorted([c for c in df.columns if c.startswith('Storey_') and 'Capacity' in c],
                           key=lambda x: int(re.search(r'\d+',x).group()))
        fixed = ['建筑ID','策略','情景','城市','实际栋数','有效制冷小时数','纯制冷能耗_kWh','全市纯制冷能耗_kWh']
        all_cols = [c for c in fixed+cap_cols if c in df.columns]
        for c in df.columns:
            if c not in all_cols: all_cols.append(c)
        df = df[all_cols]
        op = os.path.join(OUTPUT_DIR, "energy_capacity_summary.csv")
        df.to_csv(op, index=False, encoding='utf-8-sig')
        print(f"\n>> {op}  ({len(df)} rows x {len(df.columns)} cols)")
        for city in CITY_LIST:
            sub = df[df['城市']==city]
            print(f"   {city}: {len(sub)} records")
    else:
        print("[ERROR] Step 3 completed the task scan but produced no valid results.")
        return 3

    build_coincident_peak_table(tasks)
    print(f"\n>> Step 3 done. ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

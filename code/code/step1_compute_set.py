"""
Step 1: SET computation and per-capita thermal-discomfort pipeline.

Input:  data/energyplus_outputs/IndoorEnv/{scenario}/*.csv
        (legacy nested layouts are also supported)
        data/supporting_data/ClusterMap/cluster_{code}_{chn}.csv
        data/supporting_data/Population/{code}_{pinyin}_full.csv

Output: output/set_calculations/{city_pinyin}/*.xlsx
        output/set_calculations/summary_uncomfortable_hours.csv
        output/per_capita_hours/per_capita_hours_summary.csv
"""
import os
os.environ.setdefault("NUMBA_NUM_THREADS", "1")

import sys, re, glob, time, multiprocessing, importlib.util, gc
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config.paths import (
    INDOOR_ROOT, CLUSTER_MAP_ROOT, POPULATION_ROOT, EPW_ROOT, OUTPUT_ROOT,
)

BASE_INDOOR = str(INDOOR_ROOT)
BASE_CLUSTER = str(CLUSTER_MAP_ROOT)
BASE_POP = str(POPULATION_ROOT)
SET_DIR = os.path.join(str(OUTPUT_ROOT), "set_calculations")
PER_CAPITA_DIR = os.path.join(str(OUTPUT_ROOT), "per_capita_hours")

from config.parameters import (
    MET, CLO, AIR_VELOCITY, RH_LIMIT,
    SET_THRESHOLD, NIGHT_HOURS, SCENARIOS, SCENARIO_ORDER, MAX_WORKERS,
)
from core.set_calculator import calculate_constrained_rh, compute_set_vectorized, warm_up_numba
from core.city_matcher import get_storey_number, get_unique_sheet_name, parse_sheet_metadata
from core.excel_exporter import normalize_datetime_column, filter_columns_for_export
from core.input_discovery import discover_indoor_records
from core.epw_pressure import pressure_for_times, resolve_epw_file

BASELINE_LABEL = "Mixed(26/27C)"

# ── Export control ──
# Keep the detailed Excel archive, but make it faster and visible.
# You can override these in CMD/PowerShell, e.g.:
#   set EXCEL_EXPORT_WORKERS=2
#   set EXCEL_ENGINE=xlsxwriter
EXPORT_EXCEL = os.environ.get("EXPORT_EXCEL", "1") not in {"0", "false", "False", "no", "NO"}
EXCEL_ENGINE = os.environ.get("EXCEL_ENGINE", "auto").strip().lower()  # auto / xlsxwriter / openpyxl
EXCEL_EXPORT_WORKERS = int(os.environ.get("EXCEL_EXPORT_WORKERS", "2"))
EXCEL_PROGRESS_EVERY = int(os.environ.get("EXCEL_PROGRESS_EVERY", "50"))

# ── 6 cities ──
# data directories use pinyin; output uses English name
CITIES = [
    {"pinyin": "bei3jing1shi4",  "en": "Beijing",    "code": "110000", "cn": "北京市"},
    {"pinyin": "guang3zhou1shi4", "en": "Guangzhou",  "code": "440100", "cn": "广州市"},
    {"pinyin": "shang4hai3shi4",  "en": "Shanghai",   "code": "310000", "cn": "上海市"},
    {"pinyin": "shen1zhen4shi4",  "en": "Shenzhen",   "code": "440300", "cn": "深圳市"},
    {"pinyin": "wu3han4shi4",     "en": "Wuhan",      "code": "420100", "cn": "武汉市"},
    {"pinyin": "xia4men2shi4",    "en": "Xiamen",     "code": "350200", "cn": "厦门市"},
]

# ── Cooling strategies ──
# Strategy label used in output CSVs ; directory names under IndoorEnv/
STRATEGIES = [
    {"label": "S1 (Baseline_Evening27)",   "d2020": "S1a_2020_Evening27",         "dfuture": "S1b_Future_FixedCap_Evening27"},
    {"label": "S2 (FixedCap_AllDay_32_27)", "d2020": None,                          "dfuture": "S2_Future_FixedCap_AllDay_32_27"},
    {"label": "S3 (FixedCap_Evening26)",   "d2020": None,                          "dfuture": "S3_Future_FixedCap_Evening26"},
    {"label": "S4 (FixedCap_AllDay_32_26)", "d2020": None,                          "dfuture": "S4_Future_FixedCap_AllDay_32_26"},
    {"label": "S5 (AutoSize_AllDay_32_26)", "d2020": None,                          "dfuture": "S5_Future_AutoSize_AllDay_32_26"},
]

SCENARIO_FOLDERS = {folder: label for label, folder in SCENARIOS.items()}

# ── Helpers ──

def _match_scenario(dir_name: str):
    key = dir_name.strip()
    if key == "2020": return "2020 Baseline"
    for k, v in SCENARIO_FOLDERS.items():
        if k in key: return v
    return None

def _read_table(path):
    """Read CSV or Excel supporting-data tables."""
    ext = os.path.splitext(path)[1].lower()

    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(path, engine="openpyxl")

    last_err = None
    for enc in ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin1"]:
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except Exception as exc:
            last_err = exc

    raise ValueError(f"Cannot read supporting-data table {path}: {last_err}")


# ── Building processing ──

def process_single_building(args):
    csv_path, strategy_label, city_pinyin, scenario_label, epw_file = args
    try:
        for enc in ['utf-8','gbk']:
            try: df = pd.read_csv(csv_path, low_memory=False, encoding=enc, engine='c'); break
            except: continue
        else: return None, None, None
        if len(df) == 0: return None, None, None

        try: hours = df.iloc[:,0].astype(str).str.strip().str.extract(r'(\d{2}):00:00')[0].astype(int)
        except: return None, None, None

        hvac_cols = [c for c in df.columns if "HVAC_CONDITIONEDTIME_SCHEDULE" in c]
        cool_cols = [c for c in df.columns if "COOLING_PERIOD_SCHEDULE" in c]
        mask = df[hvac_cols[0]] > 0 if hvac_cols else pd.Series(True, index=df.index)
        if mask.sum() == 0: return None, None, None

        pressure_all = pressure_for_times(df.iloc[:, 0], epw_file)

        df_f = df[mask].copy().reset_index(drop=True)
        pressure_f = pressure_all[mask.to_numpy(dtype=bool)]
        cooling_f = (df.loc[mask, cool_cols[0]] > 0).values if cool_cols else np.ones(len(df_f), dtype=bool)

        try: hours_f = df_f.iloc[:,0].astype(str).str.strip().str.extract(r'(\d{2}):00:00')[0].astype(int)
        except: hours_f = pd.Series([0]*len(df_f))
        night_cooling_f = hours_f.isin(NIGHT_HOURS).values & cooling_f

        temp_cols = [c for c in df_f.columns if "Zone Air Temperature" in c]
        prefixes = sorted({c.split(":Zone Air Temperature")[0] for c in temp_cols},
                           key=lambda p: get_storey_number(p) or 99999)

        out = pd.DataFrame({'Date/Time': df_f.iloc[:,0].values, 'Outdoor_Pressure_Pa': pressure_f})
        summary_rows = []; floor_data = {}
        bld_id = os.path.basename(csv_path).replace('.csv','')

        for pfx in prefixes:
            try:
                col_ta  = next(c for c in df_f.columns if c.startswith(f"{pfx}:") and "Zone Air Temperature" in c)
                col_mrt = next(c for c in df_f.columns if c.startswith(f"{pfx}:") and "Mean Radiant Temperature" in c)
                col_hr  = next(c for c in df_f.columns if c.startswith(f"{pfx}:") and "Humidity Ratio" in c)
            except StopIteration: continue

            tdb = df_f[col_ta].values.astype(float); tr = df_f[col_mrt].values.astype(float)
            w   = df_f[col_hr].values.astype(float)
            rh  = calculate_constrained_rh(tdb, w, pressure_f, RH_LIMIT)
            set_vals = compute_set_vectorized(tdb, tr, AIR_VELOCITY, rh, MET, CLO)

            sn = get_storey_number(pfx)
            nm = f"STOREY_{sn}" if sn is not None else pfx[-10:].replace(" ","_")
            floor_data[f"{nm}_SET"] = set_vals; floor_data[f"{nm}_RH"] = rh
            floor_data[f"{nm}_Ta"] = tdb; floor_data[f"{nm}_Tr"] = tr

            ncs = set_vals[night_cooling_f]
            nt  = int(np.sum(night_cooling_f))
            uh  = int(np.nansum(ncs > SET_THRESHOLD))
            summary_rows.append({'温度基准':BASELINE_LABEL,'策略':strategy_label,'城市':city_pinyin,
                '情景':scenario_label,'建筑ID':bld_id,'楼层':nm,'夜间总时数':nt,'不舒适小时数':uh})

        if not summary_rows: return None, None, None
        out = pd.concat([out, pd.DataFrame(floor_data)], axis=1)
        return summary_rows, out, bld_id
    except Exception as e:
        print(f"   [ERROR] {os.path.basename(csv_path)}: {e}")
        return None, None, None


# ── Task builder ──

_DISCOVERED_RECORDS = None


def _get_discovered_records():
    """Scan once and support both year-first flat and legacy nested layouts."""
    global _DISCOVERED_RECORDS
    if _DISCOVERED_RECORDS is None:
        city_order = [ci["pinyin"] for ci in CITIES]
        _DISCOVERED_RECORDS = discover_indoor_records(
            BASE_INDOOR,
            city_order=city_order,
            scenario_folders=SCENARIO_FOLDERS,
            strategy_specs=STRATEGIES,
        )
        if _DISCOVERED_RECORDS:
            found_cities = []
            for city in city_order:
                if any(row["city"] == city for row in _DISCOVERED_RECORDS):
                    found_cities.append(city)
            print(
                f"  >> Discovered {len(_DISCOVERED_RECORDS)} IndoorEnv CSV files; "
                f"city order={found_cities}",
                flush=True,
            )
    return _DISCOVERED_RECORDS


def build_tasks(city_pinyin):
    tasks = []
    epw_paths = {}
    if not os.path.isdir(BASE_INDOOR):
        print(f"  [SKIP] INDOOR root not found: {BASE_INDOOR}")
        return tasks

    for row in _get_discovered_records():
        if row["city"] != city_pinyin:
            continue
        scenario_label = row["scenario_label"]
        cache_key = (city_pinyin, scenario_label)
        if cache_key not in epw_paths:
            city_cfg = next((ci for ci in CITIES if ci["pinyin"] == city_pinyin), None)
            if city_cfg is None:
                raise KeyError(f"Unknown city configuration: {city_pinyin}")
            scenario_folder = next(
                (folder for folder, label in SCENARIO_FOLDERS.items() if label == scenario_label),
                None,
            )
            if scenario_folder is None:
                raise KeyError(f"Unknown scenario label: {scenario_label}")
            epw_path = resolve_epw_file(
                EPW_ROOT,
                city=city_cfg,
                scenario_label=scenario_label,
                scenario_folder=scenario_folder,
            )
            epw_paths[cache_key] = str(epw_path)
            print(
                f"  >> EPW pressure: {city_cfg['en']} | {scenario_label} | {epw_path}",
                flush=True,
            )

        tasks.append(
            (
                str(row["path"]),
                row["strategy_label"],
                city_pinyin,
                scenario_label,
                epw_paths[cache_key],
            )
        )
    return tasks


# ── Population lookup ──

ID_CANDIDATES = [
    'BuildingID', 'buildingID', 'BUILDINGID', 'Building_ID', 'building_id',
    'uildingID', '锘緽uildingID', '锘縆uildingID', 'ï»¿BuildingID',
    'buildingid', 'bldg_id', 'BldgID', 'BLDGID', 'BLDG_ID',
    '建筑ID', '建筑id', '建筑编号', '房屋ID', '楼栋ID',
]

POP_CANDIDATES = ['popNum_2', 'Population', 'population', 'Pop', 'POP', 'pop', '人口', '总人口']


def _clean_columns(df):
    """Strip whitespace/BOM and repair common mojibake in column names."""
    def clean_one(c):
        s = str(c).replace('\ufeff', '').replace('ï»¿', '').strip()
        # UTF-8 BOM read as GBK sometimes corrupts 'BuildingID' into forms
        # like '锘緽uildingID'. Repair this specific high-impact case.
        low = s.lower()
        if 'uildingid' in low and 'buildingid' not in low:
            return 'BuildingID'
        return s

    df = df.copy()
    df.columns = [clean_one(c) for c in df.columns]
    return df


def _norm_colname(name):
    """Normalize a column name for tolerant matching."""
    return re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', str(name)).lower()


def _find_column(df, candidates, required_name, file_label, allow_contains=False):
    """Find a column by common aliases. Return the original column name or None."""
    norm_to_orig = {_norm_colname(c): c for c in df.columns}
    for cand in candidates:
        hit = norm_to_orig.get(_norm_colname(cand))
        if hit is not None:
            return hit
    if allow_contains:
        for c in df.columns:
            nc = _norm_colname(c)
            for cand in candidates:
                if _norm_colname(cand) in nc:
                    return c
    print(
        f"  [ERROR] {file_label} missing required column '{required_name}'. "
        f"Available columns: {list(df.columns)}",
        flush=True,
    )
    return None


def _standardize_building_id(df, file_label):
    """Create a standard BuildingID column from possible aliases."""
    bid_col = _find_column(df, ID_CANDIDATES, 'BuildingID', file_label, allow_contains=True)
    if bid_col is None:
        return None
    df = df.copy()
    df['BuildingID'] = (
        df[bid_col]
        .astype(str)
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
    )
    return df


def _first_existing(paths):
    for x in paths:
        if x and os.path.exists(x):
            return x
    return None


def build_population_lookup(city_pinyin, city_code, city_cn):
    en_map = {
        "bei3jing1shi4": "Beijing",
        "guang3zhou1shi4": "Guangzhou",
        "shang4hai3shi4": "Shanghai",
        "shen1zhen4shi4": "Shenzhen",
        "wu3han4shi4": "Wuhan",
        "xia4men2shi4": "Xiamen",
    }
    en = en_map.get(city_pinyin, city_pinyin)
    pinyin_no_tone = re.sub(r"\d", "", city_pinyin)

    cluster_candidates = [
    os.path.join(BASE_CLUSTER, f"cluster_{city_code}_{en}.xlsx"),
    os.path.join(BASE_CLUSTER, f"cluster_{city_code}_{city_cn}.xlsx"),
    os.path.join(BASE_CLUSTER, f"cluster_{city_code}_{city_pinyin}.xlsx"),
    os.path.join(BASE_CLUSTER, f"cluster_{city_code}_{pinyin_no_tone}.xlsx"),
    os.path.join(BASE_CLUSTER, f"cluster_{city_code}_{en}.csv"),
    os.path.join(BASE_CLUSTER, f"cluster_{city_code}_{city_cn}.csv"),
]

    pop_candidates = [
    os.path.join(BASE_POP, f"{city_code}_{en}_full.xlsx"),
    os.path.join(BASE_POP, f"{city_code}_{city_pinyin}_full.xlsx"),
    os.path.join(BASE_POP, f"{city_code}_{pinyin_no_tone}_full.xlsx"),
    os.path.join(BASE_POP, f"{city_code}_{en.lower()}_full.xlsx"),
    os.path.join(BASE_POP, f"{city_code}_{en}_full.csv"),
    os.path.join(BASE_POP, f"{city_code}_{city_pinyin}_full.csv"),
]

    mf = _first_existing(cluster_candidates)
    pf = _first_existing(pop_candidates)

    if mf is None or pf is None:
        print(
            f"  [SKIP] Population for {en}: cluster={mf is not None} pop={pf is not None}\n"
            f"         tried cluster: {cluster_candidates}\n"
            f"         tried pop: {pop_candidates}",
            flush=True,
        )
        return None

    print(f"  >> Population files for {en}:\n     cluster={mf}\n     pop={pf}", flush=True)

    dc = _clean_columns(_read_table(mf))
    dp = _clean_columns(_read_table(pf))

    dc = _standardize_building_id(dc, f"cluster map ({os.path.basename(mf)})")
    dp = _standardize_building_id(dp, f"population table ({os.path.basename(pf)})")
    if dc is None or dp is None:
        return None

    if 'landUseTyp' in dc.columns:
        dc['landUseTyp'] = dc['landUseTyp'].astype(str).str.strip()
        dc = dc[dc['landUseTyp'].str.startswith('Residential', na=False)]

    required_cluster_columns = ['Fnum', 'Cluster']
    missing_cluster_columns = [c for c in required_cluster_columns if c not in dc.columns]
    if missing_cluster_columns:
        print(
            f"  [ERROR] {en} cluster map missing required columns: {missing_cluster_columns}. "
            f"Columns: {list(dc.columns)}",
            flush=True,
        )
        return None

    pop_col = next((c for c in POP_CANDIDATES if c in dp.columns), None)
    if pop_col is None:
        pop_col = _find_column(
            dp,
            POP_CANDIDATES,
            'Population/popNum_2',
            f"population csv ({en})",
            allow_contains=True,
        )
    if pop_col is None:
        return None

    # Keep only the population field from the population table. Building
    # attributes such as Fnum, Cluster, and LandNum come exclusively from the
    # cluster map, preventing pandas from creating Fnum_x/Fnum_y suffixes.
    dp_population = dp[['BuildingID', pop_col]].copy()
    dp_population = dp_population.rename(columns={pop_col: 'Population'})

    try:
        mg = pd.merge(
            dc,
            dp_population,
            on='BuildingID',
            how='inner',
            validate='one_to_one',
        )
    except pd.errors.MergeError as exc:
        print(f"  [ERROR] Population merge is not one-to-one for {en}: {exc}", flush=True)
        return None

    if mg.empty:
        print(f"  [ERROR] Population merge empty for {en}. Check whether BuildingID formats match.", flush=True)
        return None

    mg['Cluster'] = pd.to_numeric(mg['Cluster'], errors='coerce').fillna(0).astype(int)
    mg['Fnum'] = pd.to_numeric(mg['Fnum'], errors='coerce').fillna(0).astype(int)
    mg['Population'] = pd.to_numeric(mg['Population'], errors='coerce').fillna(0)

    gk = ['Cluster', 'Fnum']
    if 'LandNum' in mg.columns:
        mg['LandNum'] = pd.to_numeric(mg['LandNum'], errors='coerce').fillna(0).astype(int)
        gk = ['LandNum', 'Cluster', 'Fnum']

    lk = mg.groupby(gk, dropna=False)['Population'].sum().reset_index()
    lk = lk.rename(columns={'Population': 'Total_Pop'})

    print(
        f"  >> Population lookup ready for {en}: rows={len(lk)}, "
        f"total_pop={lk['Total_Pop'].sum():.0f}, keys={gk}",
        flush=True,
    )
    return lk


def compute_per_capita(all_summary, city_pinyin, city_en):
    df = pd.DataFrame(all_summary)
    cdf = df[df['城市'] == city_pinyin]
    if cdf.empty:
        return []

    # Build population lookup once with the real city code/name.
    # This removes the misleading "None_Beijing_full.csv" [SKIP] message.
    pl = None
    for ci in CITIES:
        if ci['pinyin'] == city_pinyin:
            pl = build_population_lookup(city_pinyin, ci['code'], ci['cn'])
            break
    if pl is None:
        return []

    results = []
    for st in cdf['策略'].unique():
        for sc in SCENARIO_ORDER:
            sub = cdf[(cdf['策略'] == st) & (cdf['情景'] == sc)]
            if sub.empty:
                continue

            sph = 0.0
            stp = 0.0
            matched_buildings = 0
            unmatched_buildings = 0

            for bid, grp in sub.groupby('建筑ID'):
                meta = parse_sheet_metadata(bid)
                if meta is None:
                    unmatched_buildings += 1
                    continue

                bt, ci_ = meta
                fn = len(grp)

                if 'LandNum' in pl.columns:
                    mt = pl[
                        (pl['LandNum'] == bt) &
                        (pl['Cluster'] == ci_) &
                        (pl['Fnum'] == fn)
                    ]
                else:
                    mt = pl[
                        (pl['Cluster'] == ci_) &
                        (pl['Fnum'] == fn)
                    ]

                if mt.empty:
                    unmatched_buildings += 1
                    continue

                tp = float(mt['Total_Pop'].values[0])
                if tp <= 0:
                    unmatched_buildings += 1
                    continue

                matched_buildings += 1
                apf = tp / fn
                stp += tp

                for _, fr in grp.iterrows():
                    if fr['不舒适小时数'] > 0:
                        sph += apf * fr['不舒适小时数']

            hpr = sph / stp if stp > 0 else 0.0
            results.append({
                '温度基准': BASELINE_LABEL,
                '策略': st,
                '城市': city_en,
                '情景': sc,
                'Total_Person_Hours': sph,
                'Total_Population': stp,
                'Hours_Per_Resident': hpr,
                'Matched_Buildings': matched_buildings,
                'Unmatched_Buildings': unmatched_buildings,
            })

    return results


def _resolve_excel_engine():
    """Prefer xlsxwriter for speed; fall back to openpyxl if unavailable."""
    if EXCEL_ENGINE in {"xlsxwriter", "openpyxl"}:
        return EXCEL_ENGINE
    if importlib.util.find_spec("xlsxwriter") is not None:
        return "xlsxwriter"
    return "openpyxl"


def _safe_excel_name(en, st, sl):
    ssl = sl.replace(" ", "_")
    sst = st.replace(" ", "_").replace("(", "").replace(")", "")[:40]
    return f"{en}_{sst}_{ssl}.xlsx"


def write_one_excel_file(args):
    """Write one strategy-scenario workbook. Designed for parallel file-level export."""
    city_dir, en, st, cp, sl, blds, engine = args
    fname = _safe_excel_name(en, st, sl)
    out_xlsx = os.path.join(city_dir, fname)
    t0 = time.time()

    try:
        writer_kwargs = {}
        if engine == "xlsxwriter":
            writer_kwargs["engine_kwargs"] = {"options": {"strings_to_urls": False}}

        with pd.ExcelWriter(out_xlsx, engine=engine, **writer_kwargs) as w:
            used = set()
            total = len(blds)

            for j, (bid, od) in enumerate(blds, 1):
                od = normalize_datetime_column(od, reference_year=2020)
                od = filter_columns_for_export(od)
                sh = get_unique_sheet_name(used, bid)
                used.add(sh)
                od.to_excel(w, sheet_name=sh, index=False)

                if j % EXCEL_PROGRESS_EVERY == 0 or j == total:
                    print(f"      {fname}: sheets {j}/{total}", flush=True)

        sec = time.time() - t0
        return fname, len(blds), sec, None

    except Exception as e:
        sec = time.time() - t0
        return fname, len(blds), sec, str(e)


def process_city(ci):
    pinyin, en = ci["pinyin"], ci["en"]
    print(f"\n{'='*60}\n  {en} ({pinyin})\n{'='*60}", flush=True)

    tasks = build_tasks(pinyin)
    if not tasks:
        print(f"  [SKIP] No indoor CSV data for {en}", flush=True)
        return [], []

    print(f"  {len(tasks)} tasks", flush=True)
    eb = {}
    all_sm = []
    nw = min(MAX_WORKERS, os.cpu_count() or 2)
    t0 = time.time()

    # SET computation stage
    with ProcessPoolExecutor(max_workers=nw) as ex:
        fs = {ex.submit(process_single_building, t): t for t in tasks}
        done = 0
        for fu in as_completed(fs):
            done += 1
            sr, od, bid = fu.result()
            if sr is None:
                continue
            # Task tuples now contain five fields:
            # (csv_path, strategy_label, city_pinyin, scenario_label, epw_file).
            # Keep the extraction robust if extra task metadata is added later.
            task = fs[fu]
            st, cp, sl = task[1], task[2], task[3]
            eb.setdefault((st, cp, sl), []).append((bid, od))
            all_sm.extend(sr)
            if done % 100 == 0 or done == len(tasks):
                print(f"   {done}/{len(tasks)}  {time.time()-t0:.0f}s", flush=True)

    print(f"   >> SET computation finished. Workbooks to export: {len(eb)}", flush=True)

    # Compute per-capita before heavy Excel export; this also removes the old fake [SKIP] log.
    pc = compute_per_capita(all_sm, pinyin, en)
    print(f"   >> Per-capita computed: {len(pc)} rows", flush=True)

    # Detailed Excel export stage
    city_dir = os.path.join(SET_DIR, pinyin)
    os.makedirs(city_dir, exist_ok=True)
    excel_file_count = len(eb)

    if EXPORT_EXCEL:
        engine = _resolve_excel_engine()
        workers = max(1, min(EXCEL_EXPORT_WORKERS, len(eb))) if eb else 1

        export_jobs = []
        def _job_sort_key(item):
            st, cp, sl = item[0]
            scen_idx = SCENARIO_ORDER.index(sl) if sl in SCENARIO_ORDER else 999
            return (st, scen_idx, sl)

        for (st, cp, sl), blds in sorted(eb.items(), key=_job_sort_key):
            # Stable sheet order makes repeated exports easier to compare.
            blds = sorted(blds, key=lambda x: str(x[0]))
            export_jobs.append((city_dir, en, st, cp, sl, blds, engine))

        print(
            f"   >> Start Excel export: {len(export_jobs)} files, "
            f"workers={workers}, engine={engine}",
            flush=True,
        )

        with ThreadPoolExecutor(max_workers=workers) as ex:
            fs = [ex.submit(write_one_excel_file, job) for job in export_jobs]
            for i, fu in enumerate(as_completed(fs), 1):
                fname, nsheets, sec, err = fu.result()
                if err:
                    print(
                        f"   [ERROR] Excel {i}/{len(fs)} failed: "
                        f"{fname}, sheets={nsheets}, {sec:.1f}s, error={err}",
                        flush=True,
                    )
                else:
                    print(
                        f"   >> Excel {i}/{len(fs)} done: "
                        f"{fname}, sheets={nsheets}, {sec:.1f}s",
                        flush=True,
                    )

        print("   >> Excel export finished.", flush=True)
    else:
        print("   >> Excel export skipped by EXPORT_EXCEL=0.", flush=True)

    # Release detailed DataFrames before moving to next city.
    eb.clear()
    gc.collect()

    print(
        f"   Excel={excel_file_count if EXPORT_EXCEL else 0} files  "
        f"PerCapita={len(pc)} rows  {time.time()-t0:.0f}s",
        flush=True,
    )
    return all_sm, pc


def main():
    os.makedirs(SET_DIR, exist_ok=True); os.makedirs(PER_CAPITA_DIR, exist_ok=True)
    print(">> Warming up Numba ..."); warm_up_numba(); print(">> Ready.\n")
    print(f"{'='*60}\n  Six-city thermal-comfort pipeline\n  Input: {BASE_INDOOR}\n{'='*60}")

    acs, acp, skipped = [], [], []
    for ci in CITIES:
        sr, pc = process_city(ci)
        if not sr and not pc: skipped.append(ci['en'])
        acs.extend(sr); acp.extend(pc)

    if skipped:
        print(f"\n  [INFO] No usable records for {len(skipped)} cities: {', '.join(skipped)}")

    if not acs and not acp:
        print("\n[ERROR] Step 1 produced no tasks or valid results. Check the input directory structure.")
        return 2

    if acs:
        df = pd.DataFrame(acs)
        df.to_csv(os.path.join(SET_DIR, "summary_uncomfortable_hours.csv"), index=False, encoding='utf-8-sig')
        print(f"\n>> Summary: {len(df)} rows")
    if acp:
        dfp = pd.DataFrame(acp)
        dfp.to_csv(os.path.join(PER_CAPITA_DIR, "per_capita_hours_summary.csv"), index=False, encoding='utf-8-sig')
        print(f">> Per-capita: {len(dfp)} rows")
    print("\n>> Step 1 done.")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

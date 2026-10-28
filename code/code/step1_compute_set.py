"""
Step 1: SET computation and per-capita thermal-discomfort pipeline.

Input:  data/energyplus_outputs/IndoorEnv/{scenario}/*.csv
        (legacy nested layouts are also supported)
        data/epw/**/*.epw   (one EPW per city x scenario, for outdoor pressure)
        data/supporting_data/ClusterMap/ (CSV or Excel; flexible city-name lookup)
        data/supporting_data/Population/ (CSV or Excel; flexible city-name lookup)

Cities are defined in config/cities.py (MODEL_CITIES=... runs a subset).

Output: output/thermal_comfort/hourly_set/{city_english_lowercase}/*.xlsx
        output/thermal_comfort/floor_discomfort_hours.csv
        output/thermal_comfort/per_capita_discomfort_hours.csv
"""
import sys
sys.dont_write_bytecode = True   # Avoid creating __pycache__ files.
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
SET_DIR = os.path.join(str(OUTPUT_ROOT), "thermal_comfort")
PER_CAPITA_DIR = os.path.join(str(OUTPUT_ROOT), "thermal_comfort")

from config.parameters import (
    MET, CLO, AIR_VELOCITY, RH_LIMIT,
    SET_THRESHOLD, NIGHT_HOURS, SCENARIO_ORDER, MAX_WORKERS,
    STRATEGIES, SCENARIO_FOLDERS,
)
from config.cities import CITIES, CITY_BY_PINYIN, selected_cities
from core.set_calculator import calculate_constrained_rh, compute_set_vectorized, warm_up_numba
from core.city_matcher import get_storey_number, get_unique_sheet_name, parse_sheet_metadata
from core.supporting_data import (
    cluster_map_candidates, population_candidates,
    find_cluster_map, find_population_table, read_table, residential_only,
    fill_missing_fnum,
)
from core.excel_exporter import normalize_datetime_column, filter_columns_for_export
from core.input_discovery import discover_indoor_records
from core.epw_pressure import pressure_for_times, resolve_epw_file, parse_hours
from core.run_report import add_problem, reset_problems, write_problems, remove_stale

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

# Cities, strategies and scenario folders come from config/ (single source of truth).

# Problems (missing EPW, failed buildings, ...) are collected with add_problem()
# and written to output/run_problems/step1_problems.csv at the end.
SUMMARY_CSV = os.path.join(SET_DIR, "floor_discomfort_hours.csv")
PER_CAPITA_CSV = os.path.join(PER_CAPITA_DIR, "per_capita_discomfort_hours.csv")


# ── Building processing ──

def process_single_building(args):
    csv_path, strategy_label, city_pinyin, scenario_label, epw_file = args
    try:
        for enc in ['utf-8','gbk']:
            try: df = pd.read_csv(csv_path, low_memory=False, encoding=enc, engine='c'); break
            except: continue
        else: return None, None, None, "cannot read CSV (utf-8/gbk)"
        if len(df) == 0: return None, None, None, "empty CSV"

        hvac_cols = [c for c in df.columns if "HVAC_CONDITIONEDTIME_SCHEDULE" in c]
        cool_cols = [c for c in df.columns if "COOLING_PERIOD_SCHEDULE" in c]
        mask = df[hvac_cols[0]] > 0 if hvac_cols else pd.Series(True, index=df.index)
        if mask.sum() == 0: return None, None, None, "no HVAC-conditioned hours"

        pressure_all = pressure_for_times(df.iloc[:, 0], epw_file)

        df_f = df[mask].copy().reset_index(drop=True)
        pressure_f = pressure_all[mask.to_numpy(dtype=bool)]
        cooling_f = (df.loc[mask, cool_cols[0]] > 0).values if cool_cols else np.ones(len(df_f), dtype=bool)

        # Shared parser (same as the EPW alignment); raises instead of silently
        # setting every hour to 0, which used to empty the night window.
        hours_f = parse_hours(df_f.iloc[:, 0])
        night_cooling_f = np.isin(hours_f, NIGHT_HOURS) & cooling_f

        temp_cols = [c for c in df_f.columns if "Zone Air Temperature" in c]
        # NB: the old key `get_storey_number(p) or 99999` sent STOREY 0 to the end.
        def _storey_key(p):
            sn = get_storey_number(p)
            return (sn is None, sn if sn is not None else 0, p)
        prefixes = sorted({c.split(":Zone Air Temperature")[0] for c in temp_cols}, key=_storey_key)

        out = pd.DataFrame({'Date/Time': df_f.iloc[:,0].values, 'Outdoor_Pressure_Pa': pressure_f})
        summary_rows = []; floor_data = {}
        bld_id = os.path.basename(csv_path).replace('.csv','')

        for pfx in prefixes:
            cols = {}
            for key, label in (("ta", "Zone Air Temperature"), ("mrt", "Mean Radiant Temperature"), ("hr", "Humidity Ratio")):
                cols[key] = next((c for c in df_f.columns if c.startswith(f"{pfx}:") and label in c), None)
            missing = [k for k, v in cols.items() if v is None]
            if missing:
                # Skipping the floor would change the floor count used to match
                # Fnum in the population table, so fail the building instead.
                raise ValueError(f"{pfx} lacks required column(s) {missing}")
            col_ta, col_mrt, col_hr = cols["ta"], cols["mrt"], cols["hr"]

            tdb = df_f[col_ta].values.astype(float); tr = df_f[col_mrt].values.astype(float)
            w   = df_f[col_hr].values.astype(float)
            rh  = calculate_constrained_rh(tdb, w, pressure_f, RH_LIMIT)
            set_vals = compute_set_vectorized(tdb, tr, AIR_VELOCITY, rh, MET, CLO)

            n_nan = int(np.isnan(set_vals[night_cooling_f]).sum())
            if n_nan:
                # A NaN compares as "not > threshold" and would be counted as a
                # comfortable hour, so any missing value in the window is an error.
                raise ValueError(f"{pfx}: SET missing for {n_nan} night-time cooling hours")

            sn = get_storey_number(pfx)
            nm = f"STOREY_{sn}" if sn is not None else pfx[-10:].replace(" ","_")
            if f"{nm}_SET" in floor_data:
                # Several zones on one storey would otherwise overwrite each other
                # and inflate the floor count used for per-capita matching.
                raise ValueError(
                    f"More than one thermal zone maps to {nm} (prefix {pfx!r}); "
                    "the pipeline assumes one zone per storey"
                )
            floor_data[f"{nm}_SET"] = set_vals; floor_data[f"{nm}_RH"] = rh
            floor_data[f"{nm}_Ta"] = tdb; floor_data[f"{nm}_Tr"] = tr

            ncs = set_vals[night_cooling_f]
            nt  = int(np.sum(night_cooling_f))
            uh  = int(np.sum(ncs > SET_THRESHOLD))
            summary_rows.append({'Temperature_Basis':BASELINE_LABEL,'Strategy':strategy_label,'City':city_pinyin,
                'Scenario':scenario_label,'PrototypeID':bld_id,'Floor':nm,'Night_Total_Hours':nt,'Discomfort_Hours':uh})

        if not summary_rows:
            return None, None, None, "no storey with Zone Air Temperature"
        out = pd.concat([out, pd.DataFrame(floor_data)], axis=1)
        return summary_rows, out, bld_id, None
    except Exception as e:
        return None, None, None, f"{e}"


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
            city_cfg = CITY_BY_PINYIN.get(city_pinyin)
            if city_cfg is None:
                raise KeyError(f"Unknown city configuration: {city_pinyin}")
            scenario_folder = next(
                (folder for folder, label in SCENARIO_FOLDERS.items() if label == scenario_label),
                None,
            )
            if scenario_folder is None:
                raise KeyError(f"Unknown scenario label: {scenario_label}")
            try:
                epw_path = resolve_epw_file(
                    EPW_ROOT,
                    city=city_cfg,
                    scenario_label=scenario_label,
                    scenario_folder=scenario_folder,
                )
            except (FileNotFoundError, ValueError) as exc:
                # Skip this city x scenario instead of aborting all six cities.
                epw_paths[cache_key] = None
                add_problem(city_cfg["en"], scenario_label, "", "EPW", f"scenario skipped: {str(exc).splitlines()[0]}")
                print(f"  [WARN] EPW problem, skipping {city_cfg['en']} | {scenario_label}: {exc}", flush=True)
                continue
            epw_paths[cache_key] = str(epw_path)
            print(
                f"  >> EPW pressure: {city_cfg['en']} | {scenario_label} | {epw_path}",
                flush=True,
            )

        if epw_paths[cache_key] is None:
            continue
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
    'uildingID', 'BuildingID', 'BuildingID', 'ï»¿BuildingID',
    'buildingid', 'bldg_id', 'BldgID', 'BLDGID', 'BLDG_ID',
    'PrototypeID', 'BuildingID', 'BuildingID', 'BuildingID', 'BuildingID',
]

POP_CANDIDATES = ['popNum_2', 'Population', 'population', 'Pop', 'POP', 'pop', 'Population', 'TotalPopulation']


def _clean_columns(df):
    """Strip whitespace/BOM and repair common mojibake in column names."""
    def clean_one(c):
        s = str(c).replace('\ufeff', '').replace('ï»¿', '').strip()
        # UTF-8 BOM read as GBK sometimes corrupts 'BuildingID' into forms
        # Repair this known mojibake variant of the BuildingID header.
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


def build_population_lookup(city):
    """Return (lookup, stats) or (None, reason).

    lookup: DataFrame keyed by (LandNum,) Cluster, Fnum with Total_Pop.
    stats : residential buildings in the cluster map and how many of them have
            a population record (the per-capita denominator only covers those).
    """
    en = city["en"]
    cluster_candidates = cluster_map_candidates(BASE_CLUSTER, city)
    pop_candidates = population_candidates(BASE_POP, city)
    mf = find_cluster_map(BASE_CLUSTER, city)
    pf = find_population_table(BASE_POP, city)

    if mf is None or pf is None:
        print(
            f"  [SKIP] Population for {en}: cluster={mf is not None} pop={pf is not None}\n"
            f"         tried cluster: {cluster_candidates}\n"
            f"         tried pop: {pop_candidates}",
            flush=True,
        )
        return None, f"cluster map found={mf is not None}, population table found={pf is not None}"

    print(f"  >> Population files for {en}:\n     cluster={mf}\n     pop={pf}", flush=True)

    dc = _clean_columns(read_table(mf))
    dp = _clean_columns(read_table(pf))

    dc = _standardize_building_id(dc, f"cluster map ({os.path.basename(mf)})")
    dp = _standardize_building_id(dp, f"population table ({os.path.basename(pf)})")
    if dc is None or dp is None:
        return None, "BuildingID column not found in cluster map or population table"

    dc = residential_only(dc)
    # Same Fnum=0 → ceil(Height/3) rule as the Population tables and
    # load_building_counts(), so Step 1 and Step 3 key the same stock groups.
    dc = fill_missing_fnum(dc)

    required_cluster_columns = ['Fnum', 'Cluster']
    missing_cluster_columns = [c for c in required_cluster_columns if c not in dc.columns]
    if missing_cluster_columns:
        print(
            f"  [ERROR] {en} cluster map missing required columns: {missing_cluster_columns}. "
            f"Columns: {list(dc.columns)}",
            flush=True,
        )
        return None, f"cluster map missing columns {missing_cluster_columns}"

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
        return None, "population column not found"

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
        return None, f"population merge not one-to-one: {exc}"

    if mg.empty:
        print(f"  [ERROR] Population merge empty for {en}. Check whether BuildingID formats match.", flush=True)
        return None, "no BuildingID in common between cluster map and population table"

    mg['Cluster'] = pd.to_numeric(mg['Cluster'], errors='coerce').fillna(0).astype(int)
    mg['Fnum'] = pd.to_numeric(mg['Fnum'], errors='coerce').fillna(0).astype(int)
    mg['Population'] = pd.to_numeric(mg['Population'], errors='coerce').fillna(0)

    gk = ['Cluster', 'Fnum']
    if 'LandNum' in mg.columns:
        mg['LandNum'] = pd.to_numeric(mg['LandNum'], errors='coerce').fillna(0).astype(int)
        gk = ['LandNum', 'Cluster', 'Fnum']

    lk = mg.groupby(gk, dropna=False)['Population'].sum().reset_index()
    lk = lk.rename(columns={'Population': 'Total_Pop'})

    stats = {
        'residential_buildings': int(len(dc)),
        'with_population': int(len(mg)),
    }
    stats['join_rate'] = stats['with_population'] / stats['residential_buildings'] if len(dc) else 0.0
    print(
        f"  >> Population lookup ready for {en}: rows={len(lk)}, "
        f"total_pop={lk['Total_Pop'].sum():.0f}, keys={gk}; "
        f"residential buildings with a population record: "
        f"{stats['with_population']}/{stats['residential_buildings']} ({stats['join_rate']:.1%})",
        flush=True,
    )
    return lk, stats


def compute_per_capita(all_summary, city):
    city_pinyin, city_en = city["pinyin"], city["en"]
    if not all_summary:
        return []
    df = pd.DataFrame(all_summary)
    cdf = df[df['City'] == city_pinyin]
    if cdf.empty:
        return []

    pl, info = build_population_lookup(city)
    if pl is None:
        add_problem(city_en, "", "", "per-capita not computed", info)
        return []
    city_res_pop = float(pl['Total_Pop'].sum())
    if info['join_rate'] < 0.99:
        add_problem(
            city_en, "", "", "population table incomplete",
            f"only {info['with_population']}/{info['residential_buildings']} residential buildings "
            f"({info['join_rate']:.1%}) have a population record; Population_Coverage is relative to those",
        )

    results = []
    unmatched_ids = set()
    for st in cdf['Strategy'].unique():
        for sc in SCENARIO_ORDER:
            sub = cdf[(cdf['Strategy'] == st) & (cdf['Scenario'] == sc)]
            if sub.empty:
                continue

            sph = 0.0
            stp = 0.0
            matched_buildings = 0
            unmatched_buildings = 0
            seen_keys = {}
            duplicates = []

            for bid, grp in sub.groupby('PrototypeID'):
                meta = parse_sheet_metadata(bid)
                if meta is None:
                    unmatched_buildings += 1
                    unmatched_ids.add(bid)
                    continue

                bt, ci_ = meta
                fn = grp['Floor'].nunique()

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
                    unmatched_ids.add(bid)
                    continue

                tp = float(mt['Total_Pop'].values[0])
                if tp <= 0:
                    unmatched_buildings += 1
                    unmatched_ids.add(bid)
                    continue

                key = (bt, ci_, fn)
                if key in seen_keys:
                    # Two simulated prototypes share one stock group: its
                    # population would be counted twice. No defensible split
                    # exists, so this (strategy, scenario) row is not produced.
                    duplicates.append(f"{seen_keys[key]} & {bid} → (LandNum={bt}, Cluster={ci_}, Fnum={fn})")
                seen_keys[key] = bid

                matched_buildings += 1
                apf = tp / fn
                stp += tp

                for _, fr in grp.iterrows():
                    if fr['Discomfort_Hours'] > 0:
                        sph += apf * fr['Discomfort_Hours']

            if duplicates:
                add_problem(
                    city_en, sc, st, "per-capita row not produced",
                    "several prototypes share one population group: " + "; ".join(duplicates),
                )
                continue

            hpr = sph / stp if stp > 0 else 0.0
            results.append({
                'Temperature_Basis': BASELINE_LABEL,
                'Strategy': st,
                'City': city_en,
                'Scenario': sc,
                'Total_Person_Hours': sph,
                'Total_Population': stp,
                'Hours_Per_Resident': hpr,
                'Matched_Buildings': matched_buildings,
                'Unmatched_Buildings': unmatched_buildings,
                # Population of residential buildings that have a population
                # record (cluster map ∩ population table), and the share of it
                # represented by the simulated prototypes. The join rate is
                # reported separately in run_problems when below 99 %.
                'City_Residential_Population': city_res_pop,
                'Population_Coverage': stp / city_res_pop if city_res_pop > 0 else 0.0,
            })

    if unmatched_ids:
        ids = sorted(unmatched_ids)
        add_problem(
            city_en, "", "", "prototype without population group",
            f"{len(ids)} prototype(s) match no (LandNum, Cluster, Fnum) group with population, "
            f"so they represent nobody in per-capita results: {', '.join(ids[:10])}{' ...' if len(ids) > 10 else ''}",
        )
    return results


def _resolve_excel_engine():
    """Prefer xlsxwriter for speed; fall back to openpyxl if unavailable."""
    if EXCEL_ENGINE in {"xlsxwriter", "openpyxl"}:
        return EXCEL_ENGINE
    if importlib.util.find_spec("xlsxwriter") is not None:
        return "xlsxwriter"
    return "openpyxl"


def _safe_excel_name(en, st, sl):
    city = re.sub(r"[^a-z0-9]+", "_", str(en).lower()).strip("_")
    strategy = str(st).split()[0].lower()
    scenario = re.sub(r"[^a-z0-9]+", "_", str(sl).lower()).strip("_")
    return f"{city}_{strategy}_{scenario}_hourly_set.xlsx"


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
        return [], [], 0

    print(f"  {len(tasks)} tasks", flush=True)
    eb = {}
    all_sm = []
    n_failed = 0
    nw = min(MAX_WORKERS, os.cpu_count() or 2)
    t0 = time.time()

    # SET computation stage
    with ProcessPoolExecutor(max_workers=nw) as ex:
        fs = {ex.submit(process_single_building, t): t for t in tasks}
        done = 0
        for fu in as_completed(fs):
            done += 1
            sr, od, bid, err = fu.result()
            # Task tuple: (csv_path, strategy_label, city_pinyin, scenario_label, epw_file)
            task = fs[fu]
            st, cp, sl = task[1], task[2], task[3]
            if sr is None:
                n_failed += 1
                name = os.path.basename(task[0])
                print(f"   [ERROR] {name} ({sl} | {st}): {err}", flush=True)
                add_problem(en, sl, st, name, f"building skipped: {err}")
            else:
                eb.setdefault((st, cp, sl), []).append((bid, od))
                all_sm.extend(sr)
            if done % 100 == 0 or done == len(tasks):
                print(f"   {done}/{len(tasks)}  {time.time()-t0:.0f}s", flush=True)

    print(f"   >> SET computation finished. Workbooks to export: {len(eb)}", flush=True)

    # Compute per-capita before heavy Excel export; this also removes the old fake [SKIP] log.
    pc = compute_per_capita(all_sm, ci)
    print(f"   >> Per-capita computed: {len(pc)} rows", flush=True)

    # Detailed Excel export stage
    city_dir = os.path.join(SET_DIR, "hourly_set", en.lower())
    os.makedirs(city_dir, exist_ok=True)
    excel_file_count = len(eb)

    if EXPORT_EXCEL:
        # Remove this city's workbooks from earlier runs so the folder only
        # holds files from the current run.
        for old in glob.glob(os.path.join(city_dir, "*.xlsx")):
            os.remove(old)
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
                    add_problem(en, "", "", fname, f"Excel export failed: {err}")
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
        f"PerCapita={len(pc)} rows  Failed buildings={n_failed}  {time.time()-t0:.0f}s",
        flush=True,
    )
    return all_sm, pc, n_failed


def main():
    os.makedirs(SET_DIR, exist_ok=True); os.makedirs(PER_CAPITA_DIR, exist_ok=True)
    reset_problems(OUTPUT_ROOT, "step1")
    # Old summaries must not survive a run that fails to produce new ones
    # (Step 2 would otherwise pivot stale numbers).
    remove_stale(SUMMARY_CSV, PER_CAPITA_CSV)

    print(">> Warming up Numba ..."); warm_up_numba(); print(">> Ready.\n")
    cities = selected_cities()
    print(
        f"{'='*60}\n  {len(cities)}-city thermal-comfort pipeline: "
        f"{', '.join(c['en'] for c in cities)}\n  Input: {BASE_INDOOR}\n{'='*60}"
    )
    if len(cities) < len(CITIES):
        print(f"  [INFO] Subset run: summary CSVs will only contain {', '.join(c['en'] for c in cities)}")

    acs, acp = [], []
    for ci in cities:
        sr, pc, n_failed = process_city(ci)
        if not sr:
            add_problem(ci['en'], "", "", "city", "no usable IndoorEnv results")
        elif not pc:
            add_problem(ci['en'], "", "", "city", "no per-capita results")
        acs.extend(sr); acp.extend(pc)

    if acs:
        df = pd.DataFrame(acs)
        df.to_csv(SUMMARY_CSV, index=False, encoding='utf-8-sig')
        print(f"\n>> Summary: {len(df)} rows")
    if acp:
        dfp = pd.DataFrame(acp)
        dfp.to_csv(PER_CAPITA_CSV, index=False, encoding='utf-8-sig')
        print(f">> Per-capita: {len(dfp)} rows")

    n_problems = write_problems(OUTPUT_ROOT, "step1")
    if not acs and not acp:
        print("\n[ERROR] Step 1 produced no tasks or valid results. Check the input directory structure.")
        return 2
    if n_problems:
        print(f"\n{'!'*60}\n  Step 1 finished with {n_problems} problem(s) — results are INCOMPLETE\n{'!'*60}")
    print("\n>> Step 1 done.")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

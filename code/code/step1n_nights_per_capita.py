"""
Per-capita thermally disrupted nights (and hours) from the EnergyPlus IndoorEnv outputs.

Definition
    For each modeled floor, a night is counted as thermally disrupted when at least one
    of its nine sleep-period hours (EnergyPlus hour-ending stamps 23:00, 24:00, 01:00–07:00,
    i.e. 22:00–07:00) has SET > SET_THRESHOLD within the cooling period.  A night that starts
    on day d consists of 23:00 and 24:00 of day d and 01:00–07:00 of day d+1.
    Floor-level night counts are then aggregated to the city with the same population
    weights as the discomfort hours in step1 (residents of a building divided equally among
    its floors; buildings matched to a prototype by (LandNum, Cluster, Fnum)).

The SET calculation, masks (HVAC-conditioned time, cooling period, night hours), population
lookup and matching are imported from step1_compute_set.py, so hours computed here reproduce
per_capita_discomfort_hours.csv exactly; the script checks this and reports any difference.

Usage (put this file next to step1_compute_set.py):
    python step1n_nights_per_capita.py                      # S1, all scenarios, all cities
    python step1n_nights_per_capita.py --strategy S         # all strategies
    python step1n_nights_per_capita.py --scenarios "2020 Baseline" "2060 SSP5-8.5"

Output (in OUTPUT_ROOT/thermal_comfort/):
    per_capita_discomfort_nights.csv   city × strategy × scenario: hours and nights per resident
    discomfort_nights_city_average.csv   selected-city arithmetic mean and population-weighted mean
    floor_discomfort_nights.csv         prototype × floor: discomfort hours and disrupted nights
"""
import sys
sys.dont_write_bytecode = True
import os
os.environ.setdefault("NUMBA_NUM_THREADS", "1")
import argparse, time, multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import step1_compute_set as S1          # reuse the pipeline exactly (main() is not executed)


def night_ids(dt_series, hours):
    """Night index of each record (day-of-year of the evening on which the night starts)."""
    md = dt_series.astype(str).str.extract(r'(\d{1,2})/(\d{1,2})')
    doy = pd.to_datetime('2000-' + md[0] + '-' + md[1], format='%Y-%m-%d', errors='coerce').dt.dayofyear.to_numpy()
    if np.isnan(doy).any():
        raise ValueError("cannot parse month/day from Date/Time")
    h = np.asarray(hours)
    # 23:00 and 24:00 (or 0 when written as 24:00 of the same date) belong to the night starting that day;
    # 01:00–07:00 belong to the night that started the previous evening.
    return doy.astype(int) - ((h >= 1) & (h <= 7)).astype(int)


def floor_stats(args):
    """Same reading, masks and SET as step1.process_single_building; adds disrupted nights per floor."""
    csv_path, strategy_label, city_pinyin, scenario_label, epw_file = args
    try:
        for enc in ['utf-8', 'gbk']:
            try:
                df = pd.read_csv(csv_path, low_memory=False, encoding=enc, engine='c'); break
            except Exception:
                continue
        else:
            return None, "cannot read CSV (utf-8/gbk)"
        if len(df) == 0:
            return None, "empty CSV"
        hvac_cols = [c for c in df.columns if "HVAC_CONDITIONEDTIME_SCHEDULE" in c]
        cool_cols = [c for c in df.columns if "COOLING_PERIOD_SCHEDULE" in c]
        mask = df[hvac_cols[0]] > 0 if hvac_cols else pd.Series(True, index=df.index)
        if mask.sum() == 0:
            return None, "no HVAC-conditioned hours"
        pressure_all = S1.pressure_for_times(df.iloc[:, 0], epw_file)
        df_f = df[mask].copy().reset_index(drop=True)
        pressure_f = pressure_all[mask.to_numpy(dtype=bool)]
        cooling_f = (df.loc[mask, cool_cols[0]] > 0).values if cool_cols else np.ones(len(df_f), dtype=bool)
        hours_f = S1.parse_hours(df_f.iloc[:, 0])
        night_cooling_f = np.isin(hours_f, S1.NIGHT_HOURS) & cooling_f
        nid = night_ids(df_f.iloc[:, 0], hours_f)

        temp_cols = [c for c in df_f.columns if "Zone Air Temperature" in c]
        def _storey_key(p):
            sn = S1.get_storey_number(p)
            return (sn is None, sn if sn is not None else 0, p)
        prefixes = sorted({c.split(":Zone Air Temperature")[0] for c in temp_cols}, key=_storey_key)
        bld_id = os.path.basename(csv_path).replace('.csv', '')
        rows, seen = [], set()
        for pfx in prefixes:
            cols = {}
            for key, label in (("ta", "Zone Air Temperature"), ("mrt", "Mean Radiant Temperature"), ("hr", "Humidity Ratio")):
                cols[key] = next((c for c in df_f.columns if c.startswith(f"{pfx}:") and label in c), None)
            if any(v is None for v in cols.values()):
                raise ValueError(f"{pfx} lacks Ta/MRT/humidity-ratio columns")
            tdb = df_f[cols["ta"]].values.astype(float)
            tr = df_f[cols["mrt"]].values.astype(float)
            w = df_f[cols["hr"]].values.astype(float)
            rh = S1.calculate_constrained_rh(tdb, w, pressure_f, S1.RH_LIMIT)
            set_vals = S1.compute_set_vectorized(tdb, tr, S1.AIR_VELOCITY, rh, S1.MET, S1.CLO)
            if np.isnan(set_vals[night_cooling_f]).any():
                raise ValueError(f"{pfx}: SET missing in night-time cooling hours")
            sn = S1.get_storey_number(pfx)
            nm = f"STOREY_{sn}" if sn is not None else pfx[-10:].replace(" ", "_")
            if nm in seen:
                raise ValueError(f"more than one zone maps to {nm}")
            seen.add(nm)
            hot = night_cooling_f & (set_vals > S1.SET_THRESHOLD)
            rows.append({'Strategy': strategy_label, 'City': city_pinyin, 'Scenario': scenario_label, 'PrototypeID': bld_id, 'Floor': nm,
                         'Night_Total_Hours': int(night_cooling_f.sum()),
                         'Evaluated_Nights': int(np.unique(nid[night_cooling_f]).size),
                         'Discomfort_Hours': int(hot.sum()),
                         'Discomfort_Nights': int(np.unique(nid[hot]).size)})
        return rows, None
    except Exception as e:
        return None, str(e)


def per_capita(rows, city):
    """Population weighting identical to step1.compute_per_capita, applied to hours and nights."""
    df = pd.DataFrame(rows)
    pl, info = S1.build_population_lookup(city)
    if pl is None:
        print(f"  [SKIP] population lookup for {city['en']}: {info}"); return []
    city_res_pop = float(pl['Total_Pop'].sum())
    out = []
    for (st, sc), sub in df.groupby(['Strategy', 'Scenario']):
        sph = spn = stp = 0.0; matched = 0; keys = {}; dup = False
        for bid, grp in sub.groupby('PrototypeID'):
            meta = S1.parse_sheet_metadata(bid)
            if meta is None:
                continue
            bt, ci_ = meta
            fn = grp['Floor'].nunique()
            if 'LandNum' in pl.columns:
                mt = pl[(pl['LandNum'] == bt) & (pl['Cluster'] == ci_) & (pl['Fnum'] == fn)]
            else:
                mt = pl[(pl['Cluster'] == ci_) & (pl['Fnum'] == fn)]
            if mt.empty:
                continue
            tp = float(mt['Total_Pop'].values[0])
            if tp <= 0:
                continue
            if (bt, ci_, fn) in keys:
                dup = True
            keys[(bt, ci_, fn)] = bid
            matched += 1
            apf = tp / fn
            stp += tp
            sph += apf * grp['Discomfort_Hours'].sum()
            spn += apf * grp['Discomfort_Nights'].sum()
        if dup:
            print(f"  [WARN] {city['en']} {sc} {st}: several prototypes share one population group; row skipped")
            continue
        out.append({'City': city['en'], 'Strategy': st, 'Scenario': sc,
                    'Hours_Per_Resident': sph / stp if stp else np.nan,
                    'Nights_Per_Resident': spn / stp if stp else np.nan,
                    'Hours_Per_Disrupted_Night': sph / spn if spn else np.nan,
                    'Total_Population': stp, 'Matched_Buildings': matched,
                    'City_Residential_Population': city_res_pop})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strategy', default='S1', help='strategy-label prefix, e.g. S1; use S for all')
    ap.add_argument('--scenarios', nargs='*', default=None, help='scenario labels, e.g. "2020 Baseline" "2060 SSP5-8.5"')
    ap.add_argument('--workers', type=int, default=None)
    a = ap.parse_args()

    out_dir = os.path.join(str(S1.OUTPUT_ROOT), "thermal_comfort"); os.makedirs(out_dir, exist_ok=True)
    print(">> Warming up Numba ..."); S1.warm_up_numba()
    nw = a.workers or min(S1.MAX_WORKERS, os.cpu_count() or 2)
    detail, summary = [], []
    for city in S1.selected_cities():
        tasks = [t for t in S1.build_tasks(city['pinyin'])
                 if str(t[1]).startswith(a.strategy) and (a.scenarios is None or t[3] in a.scenarios)]
        if not tasks:
            print(f"  [SKIP] no tasks for {city['en']}"); continue
        print(f"\n== {city['en']}: {len(tasks)} buildings × scenarios", flush=True)
        rows, t0, nerr = [], time.time(), 0
        with ProcessPoolExecutor(max_workers=nw) as ex:
            fs = {ex.submit(floor_stats, t): t for t in tasks}
            for k, fu in enumerate(as_completed(fs), 1):
                r, err = fu.result()
                if r is None:
                    nerr += 1; print(f"   [ERROR] {os.path.basename(fs[fu][0])} ({fs[fu][3]} | {fs[fu][1]}): {err}")
                else:
                    rows.extend(r)
                if k % 100 == 0 or k == len(tasks):
                    print(f"   {k}/{len(tasks)}  {time.time() - t0:.0f}s", flush=True)
        detail.extend(rows)
        summary.extend(per_capita(rows, city))
        if nerr:
            print(f"   [WARN] {nerr} building files failed; per-capita values for {city['en']} are incomplete")

    pd.DataFrame(detail).to_csv(os.path.join(out_dir, "floor_discomfort_nights.csv"), index=False, encoding='utf-8-sig')
    sm = pd.DataFrame(summary)
    # consistency check against step1's per-capita hours
    ref_f = os.path.join(out_dir, "per_capita_discomfort_hours.csv")
    if os.path.exists(ref_f) and not sm.empty:
        ref = pd.read_csv(ref_f, encoding='utf-8-sig')[['City', 'Strategy', 'Scenario', 'Hours_Per_Resident']].rename(columns={'Hours_Per_Resident': 'Hours_step1'})
        sm = sm.merge(ref, on=['City', 'Strategy', 'Scenario'], how='left')
        sm['Hours_diff'] = sm['Hours_Per_Resident'] - sm['Hours_step1']
        bad = sm[sm['Hours_diff'].abs() > 1e-6]
        print("\n>> Check vs per_capita_discomfort_hours.csv:",
              "hours identical ✓" if bad.empty else f"{len(bad)} rows differ (see Hours_diff) — check that step1 was run with the same settings")
    sm.to_csv(os.path.join(out_dir, "per_capita_discomfort_nights.csv"), index=False, encoding='utf-8-sig')
    if not sm.empty:
        g = sm.groupby(['Strategy', 'Scenario'])
        six = pd.DataFrame({
            'n_cities': g.size(),
            'Hours_mean': g['Hours_Per_Resident'].mean(),
            'Nights_mean': g['Nights_Per_Resident'].mean(),
            'Hours_popweighted': g.apply(lambda x: np.average(x['Hours_Per_Resident'], weights=x['Total_Population'])),
            'Nights_popweighted': g.apply(lambda x: np.average(x['Nights_Per_Resident'], weights=x['Total_Population'])),
        }).reset_index()
        six.to_csv(os.path.join(out_dir, "discomfort_nights_city_average.csv"), index=False, encoding='utf-8-sig')
        print("\n>> Selected-city averages (arithmetic = n_cities mean of city values):")
        print(six.round(2).to_string(index=False))
    print(f"\n>> Written to {out_dir}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

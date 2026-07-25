"""
SET (Standard Effective Temperature) calculation:
- Compute constrained relative humidity from temperature, humidity ratio, and pressure
- Call pythermalcomfort's vectorized set_tmp interface

IMPORTANT: Callers should set NUMBA_NUM_THREADS=1 before importing pythermalcomfort
to avoid Numba multi-threading memory explosion in child processes.
"""

import os

# Must be set before importing pythermalcomfort (limits Numba threads to prevent
# memory explosion in multiprocessing)
os.environ.setdefault("NUMBA_NUM_THREADS", "1")

import numpy as np
from pythermalcomfort.models import set_tmp


def calculate_constrained_rh(tdb, w_kg_kg, p_pa, rh_limit):
    """
    Calculate relative humidity from dry-bulb temperature, humidity ratio,
    and atmospheric pressure. Clamp values exceeding rh_limit to simulate
    AC dehumidification.

    Fully vectorized — inputs are numpy arrays.

    Args:
        tdb: dry-bulb temperature [°C]
        w_kg_kg: humidity ratio [kg/kg]
        p_pa: atmospheric pressure [Pa]
        rh_limit: RH upper bound [%] (simulates AC dehumidification)

    Returns:
        constrained relative humidity array [%]
    """
    try:
        tdb = np.asarray(tdb, dtype=float)
        w = np.maximum(np.asarray(w_kg_kg, dtype=float), 1e-6)
        p = np.asarray(p_pa, dtype=float)

        # Saturation vapor pressure (Magnus formula)
        es = 611.2 * np.exp(17.67 * tdb / (tdb + 243.5))
        # Actual vapor pressure
        e = p * w / (0.62198 + w)
        rh = (e / es) * 100
        rh = np.clip(rh, 0.1, 100.0)

        # Apply AC dehumidification upper limit
        return np.where(rh > rh_limit, rh_limit, rh)
    except Exception:
        return np.full_like(np.asarray(tdb, dtype=float), np.nan)


def compute_set_vectorized(tdb, tr, v, rh, met, clo):
    """
    Vectorized SET computation using pythermalcomfort.set_tmp with array input support.

    Args:
        tdb: dry-bulb temperature array [°C]
        tr: mean radiant temperature array [°C]
        v: air velocity (scalar or array) [m/s]
        rh: relative humidity array [%]
        met: metabolic rate (scalar) [met]
        clo: clothing insulation (scalar) [clo]

    Returns:
        SET array [°C]. Positions with NaN inputs are returned as NaN
        (callers decide whether to skip them).
    """
    try:
        tdb_arr = np.asarray(tdb, dtype=float)
        tr_arr = np.asarray(tr, dtype=float)
        rh_arr = np.asarray(rh, dtype=float)

        # Mark positions where any input is NaN (SET is unreliable there)
        nan_mask = np.isnan(tdb_arr) | np.isnan(tr_arr) | np.isnan(rh_arr)

        # Fill NaN positions with placeholder values so set_tmp can compute
        tdb_calc = np.where(nan_mask, 25.0, tdb_arr)
        tr_calc = np.where(nan_mask, 25.0, tr_arr)
        rh_calc = np.where(nan_mask, 50.0, rh_arr)

        res = set_tmp(tdb=tdb_calc, tr=tr_calc, v=v, rh=rh_calc,
                      met=met, clo=clo, limit_inputs=False)

        if hasattr(res, 'set'):
            set_vals = np.array(res.set)
        elif hasattr(res, 'set_tmp'):
            set_vals = np.array(res.set_tmp)
        elif isinstance(res, dict):
            set_vals = np.array(res.get('set', np.nan))
        elif isinstance(res, (list, np.ndarray)):
            set_vals = np.array(res)
        else:
            set_vals = np.full_like(tdb_calc, float(res))

        # NaN inputs → NaN outputs
        set_vals = set_vals.astype(float)
        set_vals[nan_mask] = np.nan
        return set_vals
    except Exception:
        return np.full_like(np.asarray(tdb, dtype=float), np.nan)


def warm_up_numba():
    """
    Warm up the Numba JIT cache in the main process.

    This prevents concurrent first-time compilation in child processes,
    which would cause memory thrashing. Call once before launching
    any ProcessPoolExecutor.
    """
    try:
        set_tmp(tdb=25.0, tr=25.0, v=0.1, rh=50.0, met=1.2, clo=0.5)
    except Exception:
        pass

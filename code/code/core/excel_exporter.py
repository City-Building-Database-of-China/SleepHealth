"""
Time alignment and column filtering utilities.
Used to clean per-building hourly DataFrames before Step 1 Excel export.
"""

import re
from typing import List, Optional

import pandas as pd


# ================= Time handling =================

def normalize_datetime_column(df: pd.DataFrame, reference_year: int = 2020) -> pd.DataFrame:
    """
    Normalize the Date/Time column to a relative time axis (flattening year differences).

    Args:
        df: DataFrame containing a 'Date/Time' column
        reference_year: reference year for alignment (default 2020)

    Returns:
        DataFrame with normalized datetime (modified in-place and returned)
    """
    if 'Date/Time' not in df.columns:
        return df

    raw = df['Date/Time'].astype(str).str.strip()
    parts = raw.str.extract(
        r'(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+'
        r'(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})'
    )

    numeric = parts.apply(pd.to_numeric, errors='coerce')
    base_date = pd.to_datetime(
        {
            'year': reference_year,
            'month': numeric['month'],
            'day': numeric['day'],
        },
        errors='coerce',
    )

    # EnergyPlus uses 24:00:00 as the end of the stated calendar day.
    # Convert it to 00:00:00 on the following calendar day.
    rolls_to_next_day = numeric['hour'].eq(24)
    valid_hour = numeric['hour'].where(~rolls_to_next_day, 0)
    valid_hour = valid_hour.where(valid_hour.between(0, 23))

    df['Date/Time'] = (
        base_date
        + pd.to_timedelta(rolls_to_next_day.fillna(False).astype(int), unit='D')
        + pd.to_timedelta(valid_hour, unit='h')
        + pd.to_timedelta(numeric['minute'], unit='m')
        + pd.to_timedelta(numeric['second'], unit='s')
    )

    return df


# ================= Column filtering logic =================

def identify_top_floor(columns: List[str]) -> Optional[str]:
    """
    Identify the top-floor storey identifier from SET column names.

    Args:
        columns: list of DataFrame column names

    Returns:
        top-floor identifier (e.g. 'STOREY_10'), or None if unidentifiable
    """
    # Extract floor numbers from all SET columns
    set_pattern = re.compile(r'(STOREY_\d+)_SET')
    floor_numbers = []

    for col in columns:
        match = set_pattern.match(col)
        if match:
            floor_str = match.group(1)
            floor_num = int(floor_str.split('_')[1])
            floor_numbers.append((floor_num, floor_str))

    if not floor_numbers:
        return None

    # Return the identifier of the highest floor
    top_floor = max(floor_numbers, key=lambda x: x[0])[1]
    return top_floor


def filter_columns_for_export(df: pd.DataFrame, top_floor_id: Optional[str] = None) -> pd.DataFrame:
    """
    Filter columns by business rules:
    1. Keep Date/Time
    2. Keep SET columns for ALL floors
    3. Keep RH, Ta, Tr columns for the TOP floor only
    4. Keep Outdoor_Pressure_Pa (if present)

    Args:
        df: raw DataFrame
        top_floor_id: top-floor identifier (e.g. 'STOREY_10').
                      Auto-detected if None.

    Returns:
        filtered DataFrame
    """
    if top_floor_id is None:
        top_floor_id = identify_top_floor(df.columns.tolist())

    keep_cols = []

    # 1. Time column
    if 'Date/Time' in df.columns:
        keep_cols.append('Date/Time')

    # 2. All SET columns
    set_cols = [c for c in df.columns if c.endswith('_SET')]
    keep_cols.extend(set_cols)

    # 3. Top-floor environmental variables only (RH, Ta, Tr)
    if top_floor_id:
        env_suffixes = ['_RH', '_Ta', '_Tr']
        for suffix in env_suffixes:
            col_name = f"{top_floor_id}{suffix}"
            if col_name in df.columns:
                keep_cols.append(col_name)

    # 4. Outdoor pressure
    if 'Outdoor_Pressure_Pa' in df.columns:
        keep_cols.append('Outdoor_Pressure_Pa')

    return df[keep_cols].copy()

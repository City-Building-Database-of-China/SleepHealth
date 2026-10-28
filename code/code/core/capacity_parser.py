"""Read per-storey cooling capacity from a ``*-capacity.csv`` file."""

from __future__ import annotations

import csv
import math
from decimal import Decimal, InvalidOperation


_REQUIRED_COLUMNS = {
    "Storey",
    "Capacity_W",
    "Capacity_Source",
    "Total_Building_Area_m2",
}


def _decimal_value(raw, field: str, path, row_number: int) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{path}: invalid {field} at row {row_number}: {raw!r}") from None
    if not value.is_finite():
        raise ValueError(f"{path}: {field} must be finite at row {row_number}: {raw!r}")
    return value


def parse_cooling_capacity(path) -> dict[int, float]:
    """Return ``{storey_number: capacity_W}`` from a capacity CSV.

    The CSV has one row per storey. Duplicate storeys are rejected because
    aggregation belongs in the CSV preparation step, not in this reader.
    """
    caps: dict[int, float] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or [])
        missing = _REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"{path}: missing required CSV columns: {', '.join(sorted(missing))}")

        for row_number, row in enumerate(reader, start=2):
            storey_value = _decimal_value(row.get("Storey"), "Storey", path, row_number)
            if storey_value < 0 or storey_value != storey_value.to_integral_value():
                raise ValueError(f"{path}: Storey must be an integer >= 0 at row {row_number}: {row.get('Storey')!r}")
            storey = int(storey_value)

            capacity_value = _decimal_value(row.get("Capacity_W"), "Capacity_W", path, row_number)
            if capacity_value <= 0:
                raise ValueError(f"{path}: Capacity_W must be > 0 at row {row_number}: {row.get('Capacity_W')!r}")
            capacity = float(capacity_value)
            if not math.isfinite(capacity) or capacity <= 0:
                raise ValueError(f"{path}: Capacity_W is outside finite float range at row {row_number}")

            if storey in caps:
                raise ValueError(f"{path}: duplicate Storey {storey} at row {row_number}; one row per storey expected")
            caps[storey] = capacity

    return caps

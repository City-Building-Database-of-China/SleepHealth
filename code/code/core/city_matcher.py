"""
Building-identifier utilities for storey and archetype metadata parsing.
Also provides safe Excel sheet-name generation.
"""

import re


def parse_sheet_metadata(sheet_name):
    """
    Parse building type and cluster ID from an Excel sheet name.

    Sheet names follow the pattern: '..._<building_type>_<cluster_id>_<other>...'

    Returns:
        (building_type, cluster_id) or None if parsing fails
    """
    match = re.search(r'_(\d+)_(\d+)_', sheet_name)
    if not match:
        return None
    try:
        return int(match.group(1)), int(match.group(2))
    except ValueError:
        return None


def get_storey_number(col_or_prefix):
    """
    Extract the storey (floor) number from a column name or prefix.

    Matches patterns like 'STOREY 0', 'STOREY_0', 'STOREY0', etc.

    Returns:
        int floor number, or None if no match
    """
    match = re.search(r'STOREY\s*_?\s*(\d+)', str(col_or_prefix), re.IGNORECASE)
    return int(match.group(1)) if match else None


def get_unique_sheet_name(existing_names, base_name):
    """
    Generate a unique Excel sheet name (max 31 chars) that does not conflict
    with already-used names.

    Preserves trailing identifiers (e.g., _<num>_<num>_<year>...) when truncating.

    Args:
        existing_names: set of already-used sheet names
        base_name: desired base name

    Returns:
        A unique sheet name string (≤31 characters)
    """
    match = re.search(r'(_\d+_\d+_\d{4}.*)$', base_name)
    if match:
        suffix = match.group(1)
        prefix = base_name[:len(base_name) - len(suffix)]
        max_prefix_len = 31 - len(suffix)
        safe_name = prefix[:max_prefix_len] + suffix
    else:
        safe_name = base_name[:31]

    counter = 0
    new_name = safe_name
    while new_name in existing_names:
        counter += 1
        suffix_len = len(str(counter)) + 1
        new_name = safe_name[:31 - suffix_len] + f"_{counter}"
    return new_name

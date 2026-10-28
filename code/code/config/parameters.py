"""
Thermal-comfort parameters, scenario definitions, and execution settings.
"""

# ================= SET thermal comfort calculation parameters =================

# Metabolic rate [met] — sleeping state
MET = 0.7

# Clothing insulation [clo] — bedding insulation
CLO = 0.8

# Indoor air velocity [m/s]
AIR_VELOCITY = 0.1

# Relative humidity upper limit under AC [%] (dehumidification effect)
RH_LIMIT = 60.0

# Legacy standard pressure retained only for compatibility with external scripts.
# Step 1 reads city/scenario-specific pressure from data/epw instead.
ATMOSPHERIC_PRESSURE_PA = 101325.0

# Nighttime discomfort threshold [°C SET]
SET_THRESHOLD = 30.0

# Nighttime hours (hour indices), 24 represents 0:00 (midnight)
NIGHT_HOURS = [23, 24, 1, 2, 3, 4, 5, 6, 7]


# ================= Scenario configuration =================

# Label → EnergyPlus subfolder / filename keyword
SCENARIOS = {
    "2020 Baseline": "2020",
    "2040 SSP1-2.6": "2040-SSP1-2.6",
    "2040 SSP2-4.5": "2040-SSP2-4.5",
    "2040 SSP5-8.5": "2040-SSP5-8.5",
    "2060 SSP1-2.6": "2060-SSP1-2.6",
    "2060 SSP2-4.5": "2060-SSP2-4.5",
    "2060 SSP5-8.5": "2060-SSP5-8.5",
}

# Scenario processing order
SCENARIO_ORDER = list(SCENARIOS.keys())

BASELINE_SCENARIO = "2020 Baseline"


# ================= Cooling strategies =================
# Shared by every step. "short" is the column header used in pivot tables.
# d2020 / dfuture are directory names of the legacy nested IndoorEnv layout;
# the flat layout identifies strategies by the filename suffix (_S0 ... _S4).
# Process all strategies included in the released Beijing archives.
# Only S1 has a 2020 baseline; S1–S5 are available for future scenarios.
STRATEGIES = [
    {"label": "S1 (Baseline_Evening27)",    "short": "S1", "d2020": "S1a_2020_Evening27", "dfuture": "S1b_Future_FixedCap_Evening27"},
    {"label": "S2 (FixedCap_AllDay_32_27)", "short": "S2", "d2020": None, "dfuture": "S2_Future_FixedCap_AllDay_32_27"},
    {"label": "S3 (FixedCap_Evening26)", "short": "S3", "d2020": None, "dfuture": "S3_Future_FixedCap_Evening26"},
    {"label": "S4 (FixedCap_AllDay_32_26)", "short": "S4", "d2020": None, "dfuture": "S4_Future_FixedCap_AllDay_32_26"},
    {"label": "S5 (AutoSize_AllDay_32_26)", "short": "S5", "d2020": None, "dfuture": "S5_Future_AutoSize_AllDay_32_26"},
]
STRATEGY_LABELS = [s["label"] for s in STRATEGIES]
STRATEGY_SHORT = [s["short"] for s in STRATEGIES]

# Scenario folder name → scenario label
SCENARIO_FOLDERS = {folder: label for label, folder in SCENARIOS.items()}


# ================= Multiprocessing configuration =================

# Number of parallel worker processes (limited by Numba/pythermalcomfort memory, recommended 2-4)
MAX_WORKERS = 4

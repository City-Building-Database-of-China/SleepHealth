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


# ================= Multiprocessing configuration =================

# Number of parallel worker processes (limited by Numba/pythermalcomfort memory, recommended 2-4)
MAX_WORKERS = 4

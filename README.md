# SleepHealth: Urban Residential Sleep-Period Thermal Comfort under Climate Change

## Purpose

The data and code in this repository support the **peer review** of the manuscript submitted to *Nature Climate Change*:

**Climate warming not only amplifies cooling demand but also undermines sleep comfort**

The materials are provided so that reviewers and future users can inspect the numerical workflow, reproduce the released Beijing case, and examine the processed source data supporting the manuscript figures.

This repository contains a **three-stage post-processing pipeline**. It starts from existing EnergyPlus simulation outputs and calculates Standard Effective Temperature (SET), sleep-period thermal-discomfort hours, population-weighted summaries, cooling-energy demand, installed cooling capacity, and coincident cooling peaks. It does not generate EnergyPlus models or execute EnergyPlus simulations.

## Workflow overview

The released computational workflow is:

```text
EnergyPlus simulation outputs used as post-processing inputs
        ↓
Step 1: SET and sleep-period thermal-discomfort calculation
        ↓
Step 2: Total and per-capita analysis-ready pivot tables
        ↓
Step 3: Cooling-energy, installed-capacity, and coincident-peak summaries
```

| Stage | Description |
|---|---|
| **EnergyPlus simulation outputs** | Hourly indoor-environment outputs, cooling-energy meters, and cooling-capacity reports generated before the public post-processing workflow. |
| **Step 1** | Reconstructs indoor relative humidity, calculates hourly SET, identifies sleep-period discomfort, and produces population-weighted per-capita results. |
| **Step 2** | Converts Step 1 outputs into analysis-ready total and per-capita pivot tables. |
| **Step 3** | Summarizes cooling-energy use, installed capacity, valid cooling hours, and coincident peak cooling loads. |

Only these three computational stages are included in the public code release.

## Data availability and public-release scope

The manuscript evaluates six Chinese megacities: Beijing, Shanghai, Guangzhou, Shenzhen, Wuhan, and Xiamen. Owing to data volume and redistribution constraints, the executable public reproduction is provided for **Beijing only**.

The large Beijing EnergyPlus simulation-output package is distributed separately through Zenodo. Processed source data supporting the six-city manuscript figures are retained in this GitHub repository where applicable.

| Material | Public location | Scope |
|---|---|---|
| Three-stage post-processing code | GitHub repository | Beijing executable reproduction |
| EnergyPlus simulation outputs used as post-processing inputs | Zenodo archive | Beijing only |
| Building and population lookup tables | `code/data/supporting_data/` | Beijing only |
| Beijing residential-building shapefile | `figure/figure2/c/BJ-shp/` | Beijing only; spatial inspection and source-data transparency |
| Figure source data | `figure/figure2/`–`figure/figure6/` | Processed tables and files supporting manuscript panels |
| Rendered manuscript panels | `figure/figure2/`–`figure/figure6/` | Provided for inspection and comparison |
| Figure plotting scripts | Not distributed | Plotting code is outside the public-release scope |
| National administrative-boundary shapefiles | Not distributed | Users should obtain authoritative boundary data independently |

The executable supporting-data directories contain only the Beijing ClusterMap and population inputs. The Beijing residential-building shapefile is provided for spatial inspection and source-data transparency. It is not read by the released three-stage post-processing pipeline. The executable workflow instead uses the ClusterMap and population lookup tables under `code/data/supporting_data/`.

Only one copy of the Beijing shapefile should be retained in the final public repository, at `figure/figure2/c/BJ-shp/`. Duplicate copies under other figure folders should be removed before release.

## Beijing building data preview

![Beijing residential building data](beijing_buildings_preview.png)

The image above is a preview of the released Beijing residential-building dataset and is not a manuscript result panel.

## Repository layout

```text
SleepHealth/
├── README.md
├── requirements.txt
├── beijing_buildings_preview.png
├── code/
│   ├── README.md
│   ├── run_all.py
│   ├── code/
│   │   ├── step1_compute_set.py
│   │   ├── step2_pivot_tables.py
│   │   ├── step3_energy_capacity.py
│   │   ├── config/
│   │   │   ├── parameters.py
│   │   │   └── paths.py
│   │   └── core/
│   │       ├── city_matcher.py
│   │       ├── excel_exporter.py
│   │       └── set_calculator.py
│   ├── data/
│   │   ├── energyplus_outputs/
│   │   │   └── README.md
│   │   └── supporting_data/
│   │       ├── ClusterMap/
│   │       └── Population/
│   └── output/                         # Generated automatically after execution
└── figure/
    ├── figure2/
    │   └── c/
    │       └── BJ-shp/                 # Single released Beijing shapefile
    ├── figure3/
    ├── figure4/
    ├── figure5/
    └── figure6/
```

The `code/output/` directory is generated automatically and is not required as an input. It may be absent from a fresh clone or retained with an empty placeholder file such as `.gitkeep`.

The `figure/` directory contains processed figure source data and rendered panels. It does not contain the manuscript plotting scripts.

## External Beijing input archive

The EnergyPlus simulation outputs required to run the three-stage pipeline are stored separately on Zenodo because of their file size.

**Zenodo record:** DOI to be inserted before public release.

After downloading the archive, extract it into:

```text
code/data/energyplus_outputs/
```

The final Zenodo package must preserve the exact strategy, city, scenario, and filename structure expected by the released scripts.

### Exact directory names

**Strategy directories**

| Strategy | Directory name |
|---|---|
| S1, 2020 baseline | `S1a_2020_Evening27` |
| S1, future scenarios | `S1b_Future_FixedCap_Evening27` |
| S2 | `S2_Future_FixedCap_AllDay_32_27` |
| S3 | `S3_Future_FixedCap_Evening26` |
| S4 | `S4_Future_FixedCap_AllDay_32_26` |
| S5 | `S5_Future_AutoSize_AllDay_32_26` |

**City directory**

```text
bei3jing1shi4/
```

**Scenario directories**

```text
2020/
2040-SSP1-2.6/
2040-SSP2-4.5/
2040-SSP5-8.5/
2060-SSP1-2.6/
2060-SSP2-4.5/
2060-SSP5-8.5/
```

The same strategy, city, and scenario hierarchy must be retained under `IndoorEnv/`, `Energy/`, and `Capacity/`. Directory names and filename suffixes must not be changed after extraction.

| Folder | Contents |
|---|---|
| `IndoorEnv/` | Hourly zone air temperature, mean radiant temperature, humidity ratio, and schedule outputs used by Steps 1 and 3 |
| `Energy/` | Cooling-electricity meter outputs used by Step 3 |
| `Capacity/` | Cooling-system sizing reports used by Step 3 |

## Core-model organization

| Item | Role |
|---|---|
| `code/run_all.py` | Runs Steps 1–3 sequentially and stops if a stage fails |
| `code/code/step1_compute_set.py` | Calculates hourly SET and sleep-period discomfort and performs population-weighted aggregation |
| `code/code/step2_pivot_tables.py` | Produces analysis-ready total and per-capita pivot tables |
| `code/code/step3_energy_capacity.py` | Summarizes cooling energy, installed capacity, represented building counts, and coincident peaks |
| `code/code/config/parameters.py` | Stores fixed SET parameters, sleep-period timestamps, strategies, and scenario definitions |
| `code/code/config/paths.py` | Resolves repository-relative input and output paths |
| `code/code/core/` | Shared calculation, city-matching, and Excel-export utilities |
| `code/data/supporting_data/ClusterMap/` | Beijing building identifiers, prototype assignments, floor counts, and related lookup fields |
| `code/data/supporting_data/Population/` | Beijing building-level population inputs used for population-weighted aggregation |

`Fnum`, `LandNum`, and `Cluster` are read from the ClusterMap table. Population tables contribute building identifiers and population values only, so the merged dataset retains the canonical field name `Fnum` rather than generated suffixes such as `Fnum_x` or `Fnum_y`.

## Python environment

Python 3.10 or later is recommended.

Install the required packages from the repository root:

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux or macOS:

```bash
source .venv/bin/activate
```

Then install the dependencies:

```bash
pip install -r requirements.txt
```

The principal dependencies are:

```text
numpy>=1.24,<3
pandas>=1.5,<3
pythermalcomfort==3.7.1
openpyxl>=3.1
XlsxWriter>=3.1
```

## Running the Beijing reproduction

### 1. Download the EnergyPlus simulation outputs

Download the Beijing archive from Zenodo and extract its `IndoorEnv`, `Energy`, and `Capacity` directories into:

```text
code/data/energyplus_outputs/
```

### 2. Confirm the supporting data

The Beijing lookup tables should remain under:

```text
code/data/supporting_data/ClusterMap/
code/data/supporting_data/Population/
```

### 3. Run the complete pipeline

From the repository root, execute:

```bash
python code/run_all.py
```

The runner executes the three stages in sequence. A nonzero return code from any stage stops the workflow immediately.

## Step-by-step execution

### Step 1 — SET and sleep-period thermal discomfort

```bash
python code/code/step1_compute_set.py
```

Principal operations:

1. read hourly indoor-environment outputs;
2. reconstruct indoor relative humidity from dry-bulb temperature, humidity ratio, and atmospheric pressure;
3. calculate hourly SET using `pythermalcomfort` 3.7.1;
4. identify intervals with SET greater than 30 °C during the defined sleep period;
5. aggregate floor-level results to buildings and population-weighted city-level indicators.

Principal outputs:

```text
code/output/set_calculations/summary_uncomfortable_hours.csv
code/output/per_capita_hours/per_capita_hours_summary.csv
```

### Step 2 — Total and per-capita analysis-ready pivot tables

```bash
python code/code/step2_pivot_tables.py
```

Principal outputs:

```text
code/output/pivot_tables/total_uncomfortable_hours_pivot.xlsx
code/output/pivot_tables/per_capita_hours_pivot.xlsx
```

### Step 3 — Energy, capacity, and coincident peak

```bash
python code/code/step3_energy_capacity.py
```

Principal outputs:

```text
code/output/energy_capacity/energy_capacity_summary.csv
code/output/energy_capacity/coincident_peak_load.csv
```

## SET and sleep-period settings

The released calculation uses the following fixed settings:

| Parameter | Value |
|---|---:|
| Metabolic rate | 0.7 met |
| Clothing and bedding insulation | 0.8 clo |
| Indoor air velocity | 0.1 m s⁻¹ |
| Relative-humidity upper limit | 60% |
| Atmospheric pressure | 101,325 Pa |
| SET discomfort threshold | 30 °C |
| Analysis season | May–October |
| Sleep period represented | 22:00–07:00 |
| EnergyPlus interval-ending timestamps | 23:00, 24:00, and 01:00–07:00 |

The calculation evaluates **nine hourly intervals spanning 22:00–07:00**, represented by the EnergyPlus interval-ending timestamps 23:00, 24:00, and 01:00–07:00. Across the May–October analysis season (184 days), this yields **1,656 evaluated hourly intervals per modeled floor and scenario**.

The original EnergyPlus CSV files contain `24:00:00` rather than `00:00:00`; therefore, `24` is retained during the calculation. When results are exported to Excel, `24:00` may be displayed as `00:00` on the following calendar day, which is expected and does not alter the represented interval.

## Cooling strategies

| Strategy | Capacity assumption | Operating condition |
|---|---|---|
| **S1** | Fixed | Nighttime cooling at 27 °C |
| **S2** | Fixed | All-day cooling: 32 °C during daytime and 27 °C during nighttime |
| **S3** | Fixed | Nighttime cooling at 26 °C |
| **S4** | Fixed | All-day cooling: 32 °C during daytime and 26 °C during nighttime |
| **S5** | Autosized | All-day cooling: 32 °C during daytime and 26 °C during nighttime |

Only S1–S5 are included in the released workflow.

## Figure source data

The figure directories contain the processed data and rendered panels used for manuscript inspection:

| Folder | Main contents |
|---|---|
| `figure/figure2/` | Six-city discomfort summaries and Beijing building-level spatial source data |
| `figure/figure3/` | AC-ownership data, external benchmark data, and processed Beijing social-sensing data |
| `figure/figure4/` | Guangzhou–Shenzhen weather, discomfort, building-stock, and predictive-decomposition source data |
| `figure/figure5/` | Floor-specific and hourly top-floor-discomfort source data |
| `figure/figure6/` | Energy–comfort, capacity-density, and hourly cooling-load source data |

These materials support inspection of the manuscript figures. **No figure plotting scripts are provided.**

The Beijing building shapefile supports spatial inspection, whereas the executable three-stage workflow uses the ClusterMap and population lookup tables.


## Replication notes

Before running the workflow, confirm that:

1. the Beijing `IndoorEnv`, `Energy`, and `Capacity` folders have been extracted from the Zenodo archive into `code/data/energyplus_outputs/`;
2. the climate-condition folders and input filenames remain unchanged;
3. the corresponding `IndoorEnv`, `Energy`, and `Capacity` files refer to the same Beijing building archetypes;
4. the ClusterMap and Population datasets contain compatible `BuildingID` values;
5. the canonical floor-count field is named `Fnum`.

The `code/output/` directory is generated automatically during execution.


## Status

- The three-stage Beijing post-processing pipeline is included.
- Seven Beijing EPW files are included.
- Beijing building-cluster and population datasets are included.
- Processed source data and rendered manuscript panels are included.
- The Beijing EnergyPlus simulation-output package will be archived on Zenodo under the reserved DOI `10.5281/zenodo.21695606`.

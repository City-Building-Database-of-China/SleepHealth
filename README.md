# City-building-database-of-China

换成 睡眠 问题

## Purpose

The data and code in this repository support the **peer review** of the manuscript submitted to *Nature Climate Change*:

**Fulfilling Spatially Heterogeneous Urban Heating Demand by Climate-Adaptive Electrification**

They are provided so reviewers and editors can verify methods, reproduce key workflow steps, and inspect city-level inputs and outputs described in the paper.

## Online resources

- **[National prototype building database](http://8.166.131.116/#/)**  
  Interactive web portal for simulation outputs from the national prototype building database (China Building Energy Model Database).

- **[Shanghai building heating EUI visualization](http://8.138.56.183:8090/webgl/examples/webgl/shanghaiHeating.html)**  
  WebGL map of Shanghai showing building-level heating energy use intensity (EUI, kWh) by building type.

## Workflow overview

Building models are produced and simulated through the following pipeline:

```
City GIS inputs  →  GIS2IDF  →  ready IDF  →  EnergyPlus simulation  →  results
```

| Stage | Description |
|-------|-------------|
| **City GIS inputs** | Building footprints and attributes, local weather, and building-parameter settings. **Replication uses the Prototype sample layers** (see below). |
| **GIS2IDF** | GIS data are turned into building EnergyPlus models (geometry and associated model setup). |
| **Ready IDF** | Per-building IDF files prepared for simulation. |
| **EnergyPlus simulation** | Batch runs using the city weather file(s). |
| **Results** | Simulation outputs for analysis and comparison with published figures or online visualizations. |

## Data availability and legal notice

Please note that we are **prohibited** from distributing or uploading the original, precise geospatial datasets to public repositories under the *Surveying and Mapping Law of the People's Republic of China* and relevant national data security regulations.

In the final published version of this work, explicit longitude and latitude coordinates are stripped from distributed materials. This repository provides a **sample spatial dataset** together with the core simulation scripts.

Reviewers and future readers can run the code on this sample data to verify the logic and validity of the computational workflow without access to restricted full-resolution survey data.

## Data in this repository

Materials are organized for three pilot cities:

| City | GIS / building data | Weather |
|------|---------------------|---------|
| **Nanjing** | Included | Included |
| **Shanghai** | Included | Included |
| **Wuhan** | Included | Included |

Weather includes baseline and scenario files aligned with each city where applicable.

### GIS inputs: Prototype vs CityBuilding

Two GIS products are provided under `input/GIS/`:

| Folder | Role | Use with bundled scripts? |
|--------|------|---------------------------|
| **`Prototype/`** | Public-release **sample** building footprints (explicit lon/lat stripped or adjusted). Matches the default paths in `1_GIS2IDF.py`. | **Yes** — use this for replication and peer review. |
| **`CityBuilding/`** | **Original full-city building GIS** from the study (all buildings per pilot city). Supplied as per-city **ZIP archives** for reference only. | **No** — not used by the default workflow scripts. |

**CityBuilding ZIP archives** (under `input/GIS/CityBuilding/`):

| City | Archive |
|------|---------|
| Nanjing | `320100_nan2jing1shi4.zip` |
| Shanghai | `310000_shang4hai3shi4.zip` |
| Wuhan | `420100_wu3han4shi4.zip` |

**CityBuilding GIS visualizations** (full-city building footprints):

| Nanjing | Shanghai | Wuhan |
|---------|----------|-------|
| ![Nanjing city building GIS](input/GIS/CityBuilding/Nan2jing1shi4.png) | ![Shanghai city building GIS](input/GIS/CityBuilding/Shang4hai3shi4.png) | ![Wuhan city building GIS](input/GIS/CityBuilding/Wu3han4shi4.png) |

The archives are **not password-protected**; you can extract them directly with any standard ZIP utility.

These archives preserve the complete building layers used in the paper-scale analyses. They are included for transparency and local inspection; unzip them only if you need to examine the full datasets. **To run `1_GIS2IDF.py` and verify the computational workflow, point the script at shapefiles under `input/GIS/Prototype/`** (e.g. `320100NANJINGSHI.shp`), not at the CityBuilding layers.

### Repository layout

| Path | Contents |
|------|----------|
| **`input/GIS/Prototype/`** | Sample building footprints for workflow replication (coordinates adjusted for public release). **Input for `1_GIS2IDF.py`.** |
| **`input/GIS/CityBuilding/`** | Original full-building city GIS, packaged as the ZIP files listed above. |
| **`input/EPW/`** | Weather files by city (`NANJING`, `SHANGHAI`, `WUHAN`), including baseline and RCP scenarios. |
| **`input/Setting/`** | Non-geometry building parameters (`non_geomtry_data_all.xlsx`, `age_de/`). |
| **`demo/`** | Self-contained **demo package** (sample IDFs + pre-run results); see below. |
| **`ready_idf/`** | Full-city generated IDF files by city. |
| **`result/`** | Simulation output folders for full-city or other runs (not used for the bundled demo). |

### Demo package (`demo/`)

A small **end-to-end example** lives under a single top-level folder so reviewers can find inputs and outputs in one place:

```
demo/
├── ready_idf/          # three pre-generated IDF files (inputs to simulation)
│   ├── 320100NANJINGSHI_1.idf
│   ├── 320100NANJINGSHI_2.idf
│   └── 320100NANJINGSHI_3.idf
└── result/             # pre-run EnergyPlus outputs (one subfolder per building)
    ├── 320100NANJINGSHI_1/
    ├── 320100NANJINGSHI_2/
    └── 320100NANJINGSHI_3/
```

| Location | Contents |
|----------|----------|
| **`demo/ready_idf/`** | Three **pre-generated IDF** files for Nanjing prototype buildings, produced by the GIS2IDF workflow from `input/GIS/Prototype/`. |
| **`demo/result/`** | **Pre-run EnergyPlus results** for the same three buildings. Each subfolder (e.g. `320100NANJINGSHI_1/`) holds standard outputs such as tabular summaries (`.csv`, `Table.htm`), SQLite (`.sql`), and logs (`.err`, `.eio`). |

**How to use the demo**

- **`2_BatchSimulation.py`** defaults to `demo/ready_idf/` → `demo/result/` (weather: `input/EPW/NANJING/Nanjing_2020.epw`). Runtime stays short while exercising the batch script.
- Open **`demo/result/`** immediately to inspect a successful run, or re-run the batch and compare with the bundled outputs.
- Full-city ready IDFs remain under `ready_idf/320100NANJINGSHI/`, `ready_idf/310000SHANGHAISHI/`, and `ready_idf/420100WUHANSHI/` when you need more than the three-building demo.

## Code organization

Workflow scripts and data folders sit at the **repository root**:

| Item | Role |
|------|------|
| **`1_GIS2IDF.py`** | Generate ready IDF files from **Prototype** GIS and input settings (default paths). |
| **`2_BatchSimulation.py`** | Run EnergyPlus in batch on ready IDF files (default: `demo/ready_idf/` → `demo/result/`). |
| **`demo/`** | Bundled sample IDFs and pre-run simulation results. |
| **`input/`** | GIS shapefiles, weather (EPW), and building-parameter settings. |
| **`ready_idf/`** | Full-city generated IDF files. |
| **`result/`** | Simulation output folders for full-city runs. |

## Paths and local configuration

**Repository data paths (portable).** Neither script hard-codes your machine username, drive letter, or clone location (e.g. no `D:\OneDrive\...` paths). Both scripts set `base_dir` to the folder containing the `.py` file (`os.path.dirname(os.path.abspath(__file__))`), then build paths with `os.path.join(base_dir, "input", ...)`, `demo/`, `ready_idf/`, and `result/`. As long as you run the scripts from a normal clone of this repository, these folders resolve correctly on any OS.

**EnergyPlus install paths (machine-specific).** EnergyPlus itself is **not** shipped in this repo. `1_GIS2IDF.py` and `2_BatchSimulation.py` still use the **default Windows install layout** for version 23.1, for example `C:\EnergyPlusV23-1-0\Energy+.idd` and related files under `C:\EnergyPlusV{version}\`. If your installation is elsewhere—or you are on Linux or macOS—you must edit those EnergyPlus paths in the scripts (search for `EnergyPlusV` / `iddfile`) before running.

## Replication notes

1. Confirm inputs under `input/` (weather, settings). For GIS, use **`input/GIS/Prototype/`** only — do not point `1_GIS2IDF.py` at the CityBuilding ZIPs or full-city layers. The default example uses `input/GIS/Prototype/320100NANJINGSHI.shp`.
2. Install **EnergyPlus 23.1** and point the EnergyPlus paths in both scripts to your local `Energy+.idd` and install folder (see **Paths and local configuration** above).
3. Run **`1_GIS2IDF.py`** to produce IDFs under `ready_idf/` (default output folder: `ready_idf/320100NANJINGSHI/`).
4. Run **`2_BatchSimulation.py`** to simulate IDFs under `demo/ready_idf/` and write outputs to `demo/result/`. Pre-computed results are already under `demo/result/` for comparison.
5. Compare outputs with the paper and the online resources above.

## Status

- Workflow scripts, sample GIS inputs, weather files, full-city ready IDFs, and a self-contained `demo/` package are included at the repository root.
- Full national-scale extensions and additional QC utilities may be added as the release is finalized.

## Post-process: scale-up to city-wide buildings

`1_GIS2IDF.py` builds **prototype** IDFs from `input/GIS/Prototype/` (one file per **`bh`**, e.g. `320100NANJINGSHI_5.idf`). **City-wide scale-up** is described in the manuscript; this repository provides the GIS and IDF data used in that step, not a separate scale-up script.

| Data | Role |
|------|------|
| **CityBuilding** (unzip `input/GIS/CityBuilding/*.zip`, read `.shp`) | All individual building footprints in the city. |
| **Prototype** (`input/GIS/Prototype/*.shp`) | Prototype buildings; source of geometry and IDF index **`bh`**. |
| **`BuildID`** | Join key between Prototype and CityBuilding. |
| **`bh`** | Prototype index; matches IDF filename suffix and `1_GIS2IDF.py` output. |
| **`LandNum`** + **`Cluster`** | Together assign each building to the correct **`proptype`** (prototype type) for mapping prototype models to individual footprints. |

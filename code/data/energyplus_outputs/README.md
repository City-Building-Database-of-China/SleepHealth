# Compressed EnergyPlus Outputs

The Beijing EnergyPlus simulation outputs required for the released reproduction workflow are stored directly in this GitHub repository. Because of GitHub file-size constraints, each climate-scenario directory is provided as an individual ZIP archive.

Before running the pipeline, extract every ZIP archive **in place** inside the corresponding `IndoorEnv`, `Energy`, and `Capacity` directories. The extracted structure must be:

```text
energyplus_outputs/
├── IndoorEnv/
│   ├── 2020/
│   ├── 2040-SSP1-2.6/
│   ├── 2040-SSP2-4.5/
│   ├── 2040-SSP5-8.5/
│   ├── 2060-SSP1-2.6/
│   ├── 2060-SSP2-4.5/
│   └── 2060-SSP5-8.5/
├── Energy/
│   ├── 2020/
│   ├── 2040-SSP1-2.6/
│   ├── 2040-SSP2-4.5/
│   ├── 2040-SSP5-8.5/
│   ├── 2060-SSP1-2.6/
│   ├── 2060-SSP2-4.5/
│   └── 2060-SSP5-8.5/
└── Capacity/
    ├── 2020/
    ├── 2040-SSP1-2.6/
    ├── 2040-SSP2-4.5/
    ├── 2040-SSP5-8.5/
    ├── 2060-SSP1-2.6/
    ├── 2060-SSP2-4.5/
    └── 2060-SSP5-8.5/
```

For example, `Capacity/2020.zip` should be extracted so that `Capacity/2020/` is restored. Apply the same rule to all seven scenario archives under each of the three input categories.

The same scenario-folder names must be used under `IndoorEnv`, `Energy`, and `Capacity`. Do not rename individual folders or move files between scenarios. After successful extraction, the ZIP files may be retained or removed locally; the released scripts read the extracted directories rather than the ZIP archives.

## Folder contents

| Folder | Contents |
|---|---|
| `IndoorEnv` | Zone-level indoor temperature, mean radiant temperature, humidity ratio, and schedule outputs |
| `Energy` | EnergyPlus meter outputs used to calculate cooling electricity demand |
| `Capacity` | EnergyPlus sizing reports used to extract installed cooling capacity |

The three folders must contain matching building identifiers. For example:

```text
IndoorEnv/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0.csv
Energy/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0-meter.csv
Capacity/2040-SSP1-2.6/bei3jing1shi4_0_1_1980_S0-table.htm
```

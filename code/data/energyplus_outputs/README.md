# External EnergyPlus Outputs

The raw EnergyPlus output archive is not stored in the GitHub repository because of its size.

Download the Beijing reproduction package from the project Zenodo record and extract it into this directory:

```text
data/energyplus_outputs/
```

The extracted structure must be:

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

The same scenario-folder names must be used under `IndoorEnv`, `Energy`, and `Capacity`. Do not rename individual folders or move files between scenarios.

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


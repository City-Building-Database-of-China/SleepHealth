"""City registry — the single source of truth for every pipeline step.

To add or remove a city, edit ``CITIES`` only. Every step (1, 2, 3 and the
input check) reads this list; no other file hard-codes city names or codes.

Fields
------
pinyin : prefix of EnergyPlus result filenames, e.g. ``bei3jing1shi4_0_1_1980_S0.csv``
en     : English name, used in output tables and supporting-data filenames
code   : administrative division code, used in supporting-data filenames
label  : English supporting-data/display label

Running a subset
----------------
Set ``MODEL_CITIES`` (comma separated; English names, pinyin identifiers or city codes), e.g.::

    MODEL_CITIES=Beijing,Wuhan python run_all.py
    python run_all.py --cities Beijing,Wuhan
"""

from __future__ import annotations

import os

# Processing/output order: Beijing, Shanghai, Guangzhou, Shenzhen, Wuhan, Xiamen.
CITIES = [
    {"pinyin": "bei3jing1shi4",   "en": "Beijing",   "code": "110000", "label": 'Beijing'},
    {"pinyin": "shang4hai3shi4",  "en": "Shanghai",  "code": "310000", "label": 'Shanghai'},
    {"pinyin": "guang3zhou1shi4", "en": "Guangzhou", "code": "440100", "label": 'Guangzhou'},
    {"pinyin": "shen1zhen4shi4",  "en": "Shenzhen",  "code": "440300", "label": 'Shenzhen'},
    {"pinyin": "wu3han4shi4",     "en": "Wuhan",     "code": "420100", "label": 'Wuhan'},
    {"pinyin": "xia4men2shi4",    "en": "Xiamen",    "code": "350200", "label": 'Xiamen'},
]

CITY_BY_PINYIN = {c["pinyin"]: c for c in CITIES}


def _aliases(city: dict) -> set[str]:
    cn = city["label"]
    return {
        city["pinyin"].lower(),
        city["en"].lower(),
        city["code"],
        cn,
        cn,
    }


def to_english(name) -> str | None:
    """Map any known alias (pinyin / English / code) to the English name."""
    key = str(name).strip()
    for city in CITIES:
        if key.lower() in _aliases(city) or key in _aliases(city):
            return city["en"]
    return None


def selected_cities() -> list[dict]:
    """Cities to process, honouring the optional ``MODEL_CITIES`` filter."""
    raw = os.environ.get("MODEL_CITIES", "").strip()
    if not raw:
        return list(CITIES)
    wanted = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    unknown = [w for w in wanted if to_english(w) is None]
    if unknown:
        valid = ", ".join(c["en"] for c in CITIES)
        raise SystemExit(f"[ERROR] Unknown city in MODEL_CITIES: {unknown}. Valid: {valid}")
    wanted_en = {to_english(w) for w in wanted}
    return [c for c in CITIES if c["en"] in wanted_en]


# English-name order, used to sort output tables
CITY_ORDER_EN = [c["en"] for c in CITIES]

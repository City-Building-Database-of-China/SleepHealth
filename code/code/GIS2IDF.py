"""
GIS -> geometry-only EnergyPlus IDF.

Reads building footprints from a shapefile (or any vector file GeoPandas can read)
and writes one IDF per building that contains geometry only: one thermal zone per
storey, the floors, walls, ceilings and roof of each storey (BuildingSurface:Detailed)
with their outside boundary conditions, and one window per exterior wall
(FenestrationSurface:Detailed) sized by the window-to-wall ratio (WWR) of the wall's
orientation. Constructions, schedules, internal loads, HVAC and run settings are not
part of this tool; the settings used in the study are given in the Supplementary
Information (Section S1.5 with Tables S3-S5, and Section S3).

Command line
    python GIS2IDF.py --shp buildings.shp --out idf_geometry
    python GIS2IDF.py --shp buildings.shp --out idf_geometry --wwr 0.3 --wwr-south 0.4 --wwr-west 0
    python GIS2IDF.py --shp buildings.shp --out idf_geometry --where "usage.str.startswith('Residential')"
    python GIS2IDF.py --shp buildings.shp --out idf_geometry --ids B001 B002 --floor-height 2.8

Python
    from GIS2IDF import run
    run("buildings.shp", "idf_geometry", wwr=0.3, wwr_overrides={"south": 0.4}, where="Height >= 18")

Geometry rules
    - Storeys come from the floor-count field when it is positive, otherwise from the
      height field divided by the storey height; buildings with neither are skipped.
    - Footprints in a geographic CRS are projected to the local UTM zone, and each
      footprint is placed with its centroid at the IDF origin.
    - Only the exterior ring is used: courtyards (interior rings) are filled in, and of
      a MultiPolygon only the largest part is kept. Both cases are listed in the log.
    - Wall orientation follows the wall azimuth: north 315-45 degrees, east 45-135,
      south 135-225, west 225-315. A WWR of 0 leaves that orientation without windows.

Output
    <out>/<file stem>_<building ID>.idf for each building, and <out>/GIS2IDF_log.csv
    listing the buildings that were skipped (with the reason) or simplified.

Requires geopandas, shapely, geomeppy (with eppy) and an EnergyPlus IDD file. The IDD
is taken from --idd, else the ENERGYPLUS_IDD environment variable, else the newest
EnergyPlus in the default install folder; the IDF version is read from the IDD.
EnergyPlus 23.2 was used for the released simulations.
"""

import argparse
import csv
import glob
import io
import math
import os
import re

import geopandas as gpd
from geomeppy import IDF
from geomeppy.recipes import window_vertices_given_wall
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

IDD_SEARCH = (
    "C:/EnergyPlusV*/Energy+.idd",              # Windows
    "/Applications/EnergyPlus-*/Energy+.idd",   # macOS
    "/usr/local/EnergyPlus-*/Energy+.idd",      # Linux
)

ORIENTATIONS = (
    ("north", lambda azimuth: azimuth > 315 or azimuth <= 45),
    ("east", lambda azimuth: 45 < azimuth <= 135),
    ("south", lambda azimuth: 135 < azimuth <= 225),
    ("west", lambda azimuth: 225 < azimuth <= 315),
)

LOG_NAME = "GIS2IDF_log.csv"


def idd_version(idd_path):
    """EnergyPlus version in the IDD header, e.g. '23.2' from '!IDD_Version 23.2.0'."""
    with open(idd_path, encoding="latin-1") as f:
        match = re.match(r"!IDD_Version\s+(\d+)\.(\d+)", f.readline())
    if match is None:
        raise ValueError(f"{idd_path} does not start with an !IDD_Version line")
    return f"{match.group(1)}.{match.group(2)}"


def find_idd(idd_path=None):
    """IDD from the argument, else ENERGYPLUS_IDD, else the newest default EnergyPlus installation."""
    path = idd_path or os.environ.get("ENERGYPLUS_IDD")
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"EnergyPlus IDD not found: {path}")
        return path
    found = [p for pattern in IDD_SEARCH for p in glob.glob(pattern)]
    if not found:
        raise FileNotFoundError("No EnergyPlus installation found; pass --idd or set ENERGYPLUS_IDD")
    return os.path.normpath(max(found, key=lambda p: tuple(int(n) for n in idd_version(p).split("."))))


def resolve_wwr(wwr, overrides=None):
    """WWR of each orientation: `wwr` everywhere, replaced by `overrides` such as {"south": 0.4}."""
    overrides = overrides or {}
    unknown = set(overrides) - {key for key, _ in ORIENTATIONS}
    if unknown:
        raise ValueError(f"Unknown orientation(s) {sorted(unknown)}; use north, east, south or west")
    wwr_by_orientation = {key: overrides.get(key, wwr) for key, _ in ORIENTATIONS}
    for key, value in wwr_by_orientation.items():
        if not 0 <= value < 1:
            raise ValueError(f"WWR for {key} must be in [0, 1): {value}")
    return wwr_by_orientation


def load_footprints(shp_path):
    """Read the footprints and return them in a metric (UTM) CRS."""
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        raise ValueError(f"{shp_path} has no coordinate reference system (.prj file)")
    if gdf.crs.is_geographic:
        gdf = gdf.to_crs(gdf.estimate_utm_crs())
    return gdf


def building_name(value):
    """Building identifier as text; '85379640.0' becomes '85379640'."""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def positive_number(value):
    """The value as a float if it is a positive number, else None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def storey_count(row, floors_field, height_field, floor_height):
    """Storeys from the floor count, else from the height; None if neither is a positive number."""
    floors = positive_number(row.get(floors_field)) if floors_field else None
    if floors:
        return max(1, int(round(floors)))
    height = positive_number(row.get(height_field)) if height_field else None
    if height:
        return max(1, math.ceil(height / floor_height))
    return None


def footprint_coordinates(geometry):
    """Counter-clockwise exterior ring without the closing point, relative to the centroid.

    Also returns what was left out of the footprint (smaller parts, courtyards) for the log.
    """
    dropped = []
    if geometry.geom_type == "MultiPolygon":
        if len(geometry.geoms) > 1:
            dropped.append(f"{len(geometry.geoms) - 1} smaller part(s) dropped")
        geometry = max(geometry.geoms, key=lambda part: part.area)
    if geometry.interiors:
        dropped.append(f"{len(geometry.interiors)} courtyard(s) filled in")
    polygon = orient(geometry, sign=1.0)
    cx, cy = polygon.centroid.x, polygon.centroid.y
    coordinates = [(round(x - cx, 3), round(y - cy, 3)) for x, y in polygon.exterior.coords[:-1]]
    if len(set(coordinates)) < 3:
        raise ValueError("footprint has fewer than 3 distinct vertices at 1 mm precision")
    return coordinates, dropped


def match_storeys(idf):
    """Pair each ceiling with the floor above it.

    geomeppy only matches surfaces whose vertex lists start at the same point, which
    is not the case for a ceiling and the floor above it, so they are paired by height.
    """
    surfaces = idf.idfobjects["BUILDINGSURFACE:DETAILED"]
    floors = {round(min(z for _, _, z in s.coords), 3): s for s in surfaces if s.Surface_Type.lower() == "floor"}
    for ceiling in surfaces:
        if ceiling.Surface_Type.lower() != "ceiling":
            continue
        floor = floors.get(round(max(z for _, _, z in ceiling.coords), 3))
        if floor is None:
            continue
        for surface, other in ((ceiling, floor), (floor, ceiling)):
            surface.Outside_Boundary_Condition = "Surface"
            surface.Outside_Boundary_Condition_Object = other.Name
            surface.Sun_Exposure = "NoSun"
            surface.Wind_Exposure = "NoWind"


def add_windows(idf, wwr_by_orientation):
    """One window per exterior wall, sized by the WWR of the wall's orientation."""
    rules = idf.idfobjects["GLOBALGEOMETRYRULES"][0]
    for wall in idf.getsurfaces("wall"):
        if wall.Outside_Boundary_Condition.lower() != "outdoors":
            continue
        azimuth = wall.azimuth % 360
        wwr = next(wwr_by_orientation[key] for key, in_sector in ORIENTATIONS if in_sector(azimuth))
        if wwr <= 0:
            continue
        window = idf.newidfobject(
            "FENESTRATIONSURFACE:DETAILED",
            Name=f"{wall.Name} Window",
            Surface_Type="Window",
            Building_Surface_Name=wall.Name,
            View_Factor_to_Ground="autocalculate",
        )
        window.setcoords(window_vertices_given_wall(wall, wwr), rules)


def geometry_idf(name, coordinates, n_storeys, floor_height, wwr_by_orientation, version):
    """IDF with one zone per storey, matched surfaces and windows, without constructions."""
    idf = IDF(io.StringIO(f"Version,{version};"))
    idf.newidfobject("BUILDING", Name=name, North_Axis=0)
    idf.newidfobject(
        "GLOBALGEOMETRYRULES",
        Starting_Vertex_Position="UpperLeftCorner",
        Vertex_Entry_Direction="Counterclockwise",
        Coordinate_System="Relative",
    )
    idf.add_block(
        name=name,
        coordinates=coordinates,
        height=n_storeys * floor_height,
        num_stories=n_storeys,
        zoning="by_storey",
    )
    idf.intersect_match()   # walls and roof -> Outdoors, lowest floor -> Ground
    match_storeys(idf)      # ceilings and the floors above them -> Surface
    add_windows(idf, wwr_by_orientation)
    return idf


def skip_reason(geometry, n_storeys):
    """Why a building cannot be converted, or None if it can."""
    if n_storeys is None:
        return "no positive floor count or height"
    if geometry is None or geometry.is_empty or geometry.geom_type not in ("Polygon", "MultiPolygon"):
        return "no polygon geometry"
    if not geometry.is_valid:
        return f"invalid polygon: {explain_validity(geometry)}"
    return None


def run(shp_path, out_dir, *, id_field="BuildingID", floors_field="Fnum", height_field="Height",
        floor_height=3.0, wwr=0.3, wwr_overrides=None, idd_path=None, where=None, ids=None, limit=None):
    """Write one geometry-only IDF per building and the log; returns (IDF files written, log rows)."""
    wwr_by_orientation = resolve_wwr(wwr, wwr_overrides)
    if floor_height <= 0:
        raise ValueError(f"Storey height must be positive: {floor_height}")
    idd_path = find_idd(idd_path)
    version = idd_version(idd_path)
    IDF.setiddname(idd_path)

    gdf = load_footprints(shp_path)
    if id_field not in gdf.columns:
        raise KeyError(f"ID field '{id_field}' not in {list(gdf.columns)}")
    if floors_field not in gdf.columns and height_field not in gdf.columns:
        raise KeyError(f"Neither '{floors_field}' nor '{height_field}' is in {list(gdf.columns)}")
    if where:
        gdf = gdf.query(where, engine="python")
    if ids:
        gdf = gdf[gdf[id_field].map(building_name).isin({building_name(i) for i in ids})]
    if limit:
        gdf = gdf.head(limit)

    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(shp_path))[0]
    total, written, log = len(gdf), 0, []
    print(f"{total} buildings selected; EnergyPlus {version} IDD: {idd_path}")
    for done, (_, row) in enumerate(gdf.iterrows(), start=1):
        name = building_name(row[id_field])
        n_storeys = storey_count(row, floors_field, height_field, floor_height)
        reason = skip_reason(row.geometry, n_storeys)
        if reason is None:
            try:
                coordinates, dropped = footprint_coordinates(row.geometry)
                idf = geometry_idf(name, coordinates, n_storeys, floor_height, wwr_by_orientation, version)
                idf.saveas(os.path.join(out_dir, f"{stem}_{name}.idf"), encoding="utf-8")
                written += 1
                if dropped:
                    log.append((name, "simplified", "; ".join(dropped)))
            except Exception as error:   # one bad footprint should not stop the batch
                reason = f"{type(error).__name__}: {error}"
        if reason is not None:
            log.append((name, "skipped", reason))
        if done % 100 == 0 or done == total:
            print(f"{done}/{total} buildings processed, {written} IDF files written")

    with open(os.path.join(out_dir, LOG_NAME), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([(id_field, "status", "detail"), *log])
    skipped = sum(1 for _, status, _ in log if status == "skipped")
    print(f"Done: {written} IDF files in {out_dir}; {skipped} skipped, {len(log) - skipped} simplified (see {LOG_NAME}).")
    return written, log


def main():
    parser = argparse.ArgumentParser(description="Write geometry-only EnergyPlus IDF files from building footprints.")
    parser.add_argument("--shp", required=True, help="Building footprint file (shapefile, GeoPackage, GeoJSON)")
    parser.add_argument("--out", required=True, help="Output folder for the IDF files and the log")
    parser.add_argument("--id-field", default="BuildingID", help="Building identifier field (default: BuildingID)")
    parser.add_argument("--floors-field", default="Fnum", help="Floor-count field (default: Fnum)")
    parser.add_argument("--height-field", default="Height", help="Building height field in metres (default: Height)")
    parser.add_argument("--floor-height", type=float, default=3.0, help="Storey height in metres (default: 3)")
    parser.add_argument("--wwr", type=float, default=0.3, help="Window-to-wall ratio for all orientations (default: 0.3)")
    for key, _ in ORIENTATIONS:
        parser.add_argument(f"--wwr-{key}", type=float, help=f"Window-to-wall ratio of {key}-facing walls (overrides --wwr)")
    parser.add_argument("--idd", help="EnergyPlus IDD file (default: ENERGYPLUS_IDD, else the newest installed EnergyPlus)")
    parser.add_argument("--where", help="Attribute filter as a pandas query, e.g. \"usage == 'Residential'\"")
    parser.add_argument("--ids", nargs="+", help="Process only these building IDs")
    parser.add_argument("--limit", type=int, help="Process only the first N selected buildings")
    args = parser.parse_args()
    wwr_overrides = {key: getattr(args, f"wwr_{key}") for key, _ in ORIENTATIONS
                     if getattr(args, f"wwr_{key}") is not None}
    run(args.shp, args.out, id_field=args.id_field, floors_field=args.floors_field, height_field=args.height_field,
        floor_height=args.floor_height, wwr=args.wwr, wwr_overrides=wwr_overrides, idd_path=args.idd,
        where=args.where, ids=args.ids, limit=args.limit)


if __name__ == "__main__":
    main()

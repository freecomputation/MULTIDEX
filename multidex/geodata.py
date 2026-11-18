"""Coarse global geography shipped as data: a land and water grid
and a weather-radio service-area grid, at a quarter degree.

Provenance. The land and water grid is rasterized from Natural
Earth's 1:110m land polygons (file ne_110m_land.geojson) and the
service-area grid from the same scale's admin-0 countries file
(ne_110m_admin_0_countries.geojson), taking the features named
"United States of America" and "Canada". Both files come from
Natural Earth (naturalearthdata.com) as published in the
natural-earth-vector repository, retrieved 2026-11-18. Natural
Earth's terms of use place all of its raster and vector data in
the public domain; no permission is needed and credit is not
required, and the source is named here anyway because a dataset
whose origin cannot be stated has no business deciding anything.

Method and the conservative rules. Each grid is 1440 by 720 cells
of 0.25 degrees, row 0 at the north, bit-packed row by row, most
significant bit first, zlib-compressed and base64-encoded below.
Cells were filled by a scanline even-odd test of every polygon
ring at each row's centre latitude. A cell counts as water when
it is not land, and also when it is land but touches a non-land
cell on any of its four sides: a coastline cell is one the source
leaves uncertain, and uncertainty counts as water. The service
grid is dilated by the same one cell in the opposite direction,
so a home reads as outside the service area only when its cell
is service-free even after that dilation. Distances to water are
measured to the nearest water cell's centre and then reduced by
GRID_UNCERTAINTY_KM, the grid's positional uncertainty (half a
cell diagonal at the equator, rounded up), so a distance this
module reports is one the grid can stand behind.

The dataset is global public geography. It carries no operator
information of any kind; a home position is only ever read
against it, never written into it.
"""

import base64
import math
import zlib

RES_DEG = 0.25
COLS = 1440
ROWS = 720
GRID_UNCERTAINTY_KM = 20.0

WATER_B64 = (
    'eNrtnT3PHTd2gGc0C48LZ7lAihjIwuMgxbZOFQfYiP4n2S6tunUhiPQqiJJKZUrlJwRIn5eCgrzphPyBaAQVKgKsRqtCNEyR'
    'mQ/OB+eDw0Ny7n3v4hK7tnV178wzZ845PDw8JJW6tmu7tmu7tmu7tmu7tmu7tmu7tmu7tmu7tms7XaPk0ojLpG13lo8lPEmK'
    '6SfJ0O4msRz48EzITcvuIvItH/h6UQv9Z6LupkqL5JseGXWfcCXZnVbmUcitlGsJkyT7VN5lOU+QG22m+j/Z8OH9vFb44m5B'
    'J4ack2VDjUHeLWg29WpyhTmnd8t3iMRwdGvIIbZY4kOEjEf/bHhlU9S+tpIdoszpoNPZ4JUXbc15vJ38d4W31C6+22kEnPZy'
    'Rpuqka7gpGufLiOWA2y3cW04+bp3zmydOVt3N7syrLy1ytbeN6Ioh0hjQ5/R7FePeepo4Yf0pLUoUDWo7Iaci8VvEuxo4ukB'
    'zLVkcTV4M7rOjJddpyMLi8ssil4SZc9A3dyGhASoVRbVZ+D2Peey7zWoa49ClypueZFRXcdjmQ4Rf25BTlaMwBmExe4KXw1a'
    'rLb77RXbb1QcqRO3pzM7TGdR6Q4zq/vMXJ2xPa169eSuzOVZgRvV+Lr3DMI13AAxx7BBli2DfmWT89JbCYAyxzBCOfFTHGvb'
    'I8pihCTsdhHkbDh53nyQiw6LRgj4OVmON8M9DG3fVQv+t/Wbm0iBZ6vjQYicebbiyz+LEfo/bv9X+7j6ksVWSslnlLJEZqtR'
    'ls8gMFOva7wva2aOlnLxVg42/6YQa2b8xi+lQaqkHcC2/1/EvJ7MfK5F1epIR/hoeJUUqKyvIlL1UdSvrZxeQ7oNU1YHl/ly'
    'ELSiXsI5/J5drlMwmcqFGKivPlczMfOtN1XfgXyCM6PB1/E5Vekr59n3xPZA54jR1v6w2/Lu1p79lElHkJRmqsHAD709bkC7'
    'nnBnOLj1rKnaknMosyk3YU+UQuQ8D71YPN0wk5xsBYj6yXn2Ag2fmb0MlXN38WeNc6K1Er5QldGzMD91nkYuC+eT06b/8oxK'
    '5fCmntXWpmimeF73X/eK7fthxwsTq8OsH114xkq8ccaNO87bfrsO8lhW94vTwQT3krNpguVaAo17huJVkzfK3itCb5r4gBHF'
    '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'
    '2q7t2q7t2q7t2q7t2q7tvO3/AWPY7NI='
)

SERVICE_B64 = (
    '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'
    'AAAAAAAAAAAAAACADfwG2c3qMw=='
)


_CACHE = {}


def _mask(name, b64):
    if name not in _CACHE:
        _CACHE[name] = zlib.decompress(base64.b64decode(b64))
    return _CACHE[name]


def _cell_of(lat, lon):
    row = int((90.0 - lat) / RES_DEG)
    col = int((lon + 180.0) / RES_DEG) % COLS
    return min(ROWS - 1, max(0, row)), col


def _bit(mask, row, col):
    i = row * COLS + col
    return (mask[i >> 3] >> (7 - (i & 7))) & 1


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) \
        * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def is_water(lat, lon):
    """True when the grid cell holding the position counts as
    water, coastline cells included (see the module docstring)."""
    row, col = _cell_of(lat, lon)
    return bool(_bit(_mask("water", WATER_B64), row, col))


def nearest_water_km(lat, lon):
    """Distance in kilometres from the position to the nearest
    water cell, with the grid's uncertainty already deducted and
    the result floored at zero: the figure a range test may take
    at face value. Rows are visited in order of increasing
    latitude distance, and the scan stops once a whole latitude
    band's nearest possible cell is farther than the best found,
    so the answer is exact against the grid, not sampled."""
    water = _mask("water", WATER_B64)
    row0, col0 = _cell_of(lat, lon)
    if _bit(water, row0, col0):
        return 0.0
    best = None
    rows = sorted(range(ROWS),
                  key=lambda r: abs((90.0 - (r + 0.5) * RES_DEG) - lat))
    for row in rows:
        clat = 90.0 - (row + 0.5) * RES_DEG
        if best is not None and (abs(clat - lat) - RES_DEG / 2) \
                * 110.6 > best + GRID_UNCERTAINTY_KM + 5.0:
            break
        for col in range(COLS):
            if _bit(water, row, col):
                d = _haversine_km(lat, lon, clat,
                                  -180.0 + (col + 0.5) * RES_DEG)
                if best is None or d < best:
                    best = d
    if best is None:
        return 0.0
    return max(0.0, best - GRID_UNCERTAINTY_KM)


def in_wxradio_service_area(lat, lon):
    """True when the position's cell falls inside the weather
    radio service area as drawn (the United States and Canada,
    dilated one cell; see the module docstring)."""
    row, col = _cell_of(lat, lon)
    return bool(_bit(_mask("service", SERVICE_B64), row, col))

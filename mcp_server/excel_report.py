from __future__ import annotations
import os
import warnings
import openpyxl
from datetime import datetime
from openpyxl.utils import get_column_letter, column_index_from_string
from mcp_server.models import ReportPayload, ReportComp, Comp, Subject

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "sf_uw_template.xlsx")


def load_template() -> "openpyxl.Workbook":
    """Load a fresh, writable copy of the KV underwriter template. Read-only on disk —
    callers mutate the returned in-memory workbook and save elsewhere."""
    if not os.path.isfile(TEMPLATE_PATH):
        raise FileNotFoundError(f"KV Excel template missing at {TEMPLATE_PATH}")
    with warnings.catch_warnings():
        # openpyxl warns that the template's Data Validation extension is dropped; harmless.
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(TEMPLATE_PATH)


ROWS = {
    "address": 6, "source": 7, "price": 9, "list_sale": 11, "neighbourhood": 13,
    "unit_price": 14, "sale_date": 15, "floor_area": 17, "site_size": 18, "style": 19,
    "format": 20, "year_built": 21, "beds": 22, "baths": 23, "basement": 24,
    "parking": 25, "fireplace": 26, "deck": 27, "appliances": 28, "notes": 29,
    "appraised_list": 30, "bidask": 31, "adj_time": 34, "adj_size": 35, "adj_garage": 36,
    "adj_beds": 37, "adj_baths": 38, "adj_finishing": 39, "adj_basement": 40,
    "adj_appliances": 41, "adj_fireplace": 42, "adj_neighbourhood": 43, "adj_deck": 44,
    "adj_other": 45, "total_adj": 46, "adjusted_price": 48, "adjusted_unit": 49,
}
# Rows that hold a per-comp formula in column E of the template (reused for Option A).
FORMULA_ROWS = (ROWS["unit_price"], ROWS["appraised_list"], ROWS["bidask"],
                ROWS["adj_time"], ROWS["adj_size"], ROWS["adj_garage"], ROWS["adj_beds"],
                ROWS["adj_baths"], ROWS["adj_finishing"], ROWS["adj_basement"],
                ROWS["adj_appliances"], ROWS["adj_fireplace"], ROWS["adj_neighbourhood"],
                ROWS["adj_deck"], ROWS["adj_other"], ROWS["total_adj"],
                ROWS["adjusted_price"], ROWS["adjusted_unit"])

_FIRST_COMP_COL = "E"          # subject is D; comps start at E
_CLEAR_LAST_COL = "P"          # sample comps + stray notes live in E..P
_CLEAR_ROWS = range(5, 50)     # attribute + adjustment block


def _set(ws, col: str, row: int, val) -> None:
    ws[f"{col}{row}"] = val


def _capture_formulas(ws) -> dict:
    """Read column-E formula strings before clearing, so Option A can re-instantiate them
    for every comp column (including ones past the template's K)."""
    out = {}
    for r in FORMULA_ROWS:
        v = ws[f"E{r}"].value
        if isinstance(v, str) and v.startswith("="):
            out[r] = v
    return out


def _clear_comp_region(ws) -> None:
    first = column_index_from_string(_FIRST_COMP_COL)
    last = column_index_from_string(_CLEAR_LAST_COL)
    for r in _CLEAR_ROWS:
        for ci in range(first, last + 1):
            ws.cell(row=r, column=ci).value = None


def _fill_attr_col(ws, col: str, c: Comp, *, source: str = "HD") -> None:
    _set(ws, col, ROWS["address"], c.address)
    _set(ws, col, ROWS["source"], source)
    _set(ws, col, ROWS["price"], c.sold_price)
    _set(ws, col, ROWS["list_sale"], "Sale Price")
    sd = ws[f"{col}{ROWS['sale_date']}"]
    sd.value = datetime(c.sold_date.year, c.sold_date.month, c.sold_date.day)
    sd.number_format = "m/d/yyyy"
    _set(ws, col, ROWS["floor_area"], c.sqft)
    if c.year_built is not None:
        _set(ws, col, ROWS["year_built"], c.year_built)
    if c.beds is not None:
        _set(ws, col, ROWS["beds"], c.beds)
    if c.baths is not None:
        _set(ws, col, ROWS["baths"], c.baths)
    if c.parking_type:
        _set(ws, col, ROWS["parking"], c.parking_type)
    if c.include_reason:
        _set(ws, col, ROWS["notes"], c.include_reason)


def _fill_subject_col(ws, s: Subject) -> None:
    _set(ws, "D", ROWS["address"], s.resolved_address or s.address)
    if s.community:
        _set(ws, "D", ROWS["neighbourhood"], s.community)
    if s.sqft is not None:
        _set(ws, "D", ROWS["floor_area"], s.sqft)
    if s.lot_sf is not None:
        _set(ws, "D", ROWS["site_size"], s.lot_sf)
    if s.year_built is not None:
        _set(ws, "D", ROWS["year_built"], s.year_built)
    if s.beds is not None:
        _set(ws, "D", ROWS["beds"], s.beds)
    if s.baths is not None:
        _set(ws, "D", ROWS["baths"], s.baths)
    if s.parking_type:
        _set(ws, "D", ROWS["parking"], s.parking_type)


def fill_comp_grid(ws, payload: ReportPayload) -> dict:
    kept = [rc for rc in payload.comps if rc.kept]
    kept.sort(key=lambda rc: (rc.comp.distance_km is None, rc.comp.distance_km or 0))
    excluded = [rc for rc in payload.comps if not rc.kept]

    formulas = _capture_formulas(ws)
    _clear_comp_region(ws)
    _fill_subject_col(ws, payload.subject)

    cols: list[str] = []
    idx = column_index_from_string(_FIRST_COMP_COL)
    for rc in kept:
        col = get_column_letter(idx)
        _fill_attr_col(ws, col, rc.comp)
        cols.append(col)
        idx += 1

    excluded_cols: list[str] = []
    if excluded:
        idx += 1  # one-column gap separates kept from excluded
        header_col = get_column_letter(idx - 1)
        ws[f"{header_col}4"] = "Excluded"
        for rc in excluded:
            col = get_column_letter(idx)
            _fill_attr_col(ws, col, rc.comp)
            if rc.exclude_reason:
                _set(ws, col, ROWS["notes"], rc.exclude_reason)
            excluded_cols.append(col)
            idx += 1

    last_col = get_column_letter(idx - 1)
    return {"cols": cols, "excluded_cols": excluded_cols,
            "last_col": last_col, "formulas": formulas}

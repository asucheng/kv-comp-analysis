from __future__ import annotations
import os
import warnings
import openpyxl

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

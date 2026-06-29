import os
import openpyxl
from mcp_server.excel_report import TEMPLATE_PATH, load_template


def test_template_is_vendored_and_loads():
    assert os.path.isfile(TEMPLATE_PATH)
    wb = load_template()
    assert "Property Comparables" in wb.sheetnames
    assert "Summary" in wb.sheetnames
    # anchor cells the rest of the code depends on
    pc = wb["Property Comparables"]
    assert pc["B6"].value == "Address"
    assert pc["B65"].value == "KV Internal Value"

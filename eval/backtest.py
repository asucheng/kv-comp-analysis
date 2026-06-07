from __future__ import annotations
from datetime import date
from statistics import median
from pydantic import BaseModel
from mcp_server.models import Subject, Criteria, AdjustmentRules
from mcp_server.compsource.base import CompSource
from mcp_server.comps import find_with_ladder
from mcp_server.estimate import reconcile


class PropertyError(BaseModel):
    address: str
    actual: float
    predicted: float
    abs_pct_error: float


class BacktestResult(BaseModel):
    n: int
    median_abs_pct_error: float
    per_property: list[PropertyError]


def hold_one_out(source: CompSource, *, community: str, as_of: date) -> BacktestResult:
    """For each real sale, hide it, predict from the others, compare to actual."""
    sales = source.recent_sales(community, lookback_months=12, as_of=as_of)
    rows: list[PropertyError] = []
    for i, target in enumerate(sales):
        others = [c for j, c in enumerate(sales) if j != i]
        subject = Subject(address=target.address, community=community,
                          lat=target.lat, lng=target.lng, sqft=target.sqft,
                          year_built=target.year_built, property_type=target.property_type)
        found = find_with_ladder(subject, others, Criteria(), as_of=as_of)
        if len(found.comps) < Criteria().min_comps:
            continue
        est = reconcile(subject, found.comps, AdjustmentRules(),
                        as_of=as_of, ladder_depth=len(found.relaxations))
        err = abs(est.point - target.sold_price) / target.sold_price * 100
        rows.append(PropertyError(address=target.address, actual=target.sold_price,
                                  predicted=est.point, abs_pct_error=round(err, 1)))
    med = round(median([r.abs_pct_error for r in rows]), 1) if rows else 0.0
    return BacktestResult(n=len(rows), median_abs_pct_error=med, per_property=rows)

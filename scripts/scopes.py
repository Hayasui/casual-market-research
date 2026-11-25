"""Explicit statistical scope and decimal arithmetic, independent of source APIs."""
from __future__ import annotations
import calendar
from datetime import date
from decimal import Decimal, InvalidOperation
from common import digest


def decimal_value(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("numeric value required")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("numeric value required") from None
    if not result.is_finite():
        raise ValueError("finite numeric value required")
    return result


def number(value):
    value = decimal_value(value)
    return int(value) if value == value.to_integral_value() else float(value)


def month_period(month):
    start = date.fromisoformat(month + "-01")
    return {"kind": "month", "start": start.isoformat(),
            "end": start.replace(day=calendar.monthrange(start.year, start.month)[1]).isoformat()}


def scope_signature(row):
    """Only explicitly declared measurement dimensions participate in compatibility."""
    basis = "revenue_basis" if row["metric"] == "revenue" else "download_basis"
    return digest({"unit": row.get("unit"), basis: row.get(basis, "unknown"),
                   "metric_definition": row.get("metric_definition", {}),
                   "countries": sorted(row.get("geography", {}).get("countries", [])),
                   "stores": sorted(row.get("store_scope", {}).get("stores", []))})

"""Market arithmetic over explicit ST records, without a saved research run."""
from copy import deepcopy
from datetime import date
import re

from common import ident, months
from report_values import derive
from scopes import decimal_value, month_period, number, scope_signature
from source_metrics import FIELDS, select_cell, series_key, source_totals, trends, yoy_pair


def normalize_records(records):
    """Check supplied scope metadata and preserve each record's source references."""
    output, ids = [], set()
    for original in records:
        row = deepcopy(original)
        rid = ident(row["id"])
        if rid in ids:
            raise ValueError("duplicate market record ID: " + rid)
        ids.add(rid)
        if row.get("source_group") != "Sensor Tower":
            raise ValueError("market calculations require Sensor Tower")
        if row.get("entity_type") not in {"product", "category"} or row.get("metric") not in {"revenue", "downloads"}:
            raise ValueError("market record needs product/category and revenue/downloads")
        if any(not isinstance(row.get(k), str) or not row[k] for k in FIELDS):
            raise ValueError("market record requires explicit entity, region, store and metric")
        for field, member in (("geography", "countries"), ("store_scope", "stores")):
            values = row.get(field, {}).get(member)
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
                raise ValueError("market record needs explicit " + field + "." + member)
        basis = "revenue_basis" if row["metric"] == "revenue" else "download_basis"
        if not isinstance(row.get(basis), str) or not row[basis]:
            raise ValueError("market record requires " + basis + "; unknown is allowed")
        if row["metric"] == "downloads" and row.get("unit") != "downloads":
            raise ValueError("downloads must use the downloads unit")
        if row["metric"] == "revenue" and not re.fullmatch(r"[A-Z]{3}", row.get("unit", "")):
            raise ValueError("revenue requires an explicit currency unit")
        if "period" not in row and row.get("month"):
            row["period"] = month_period(row["month"])
        period = row["period"]
        start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
        if start > end or period.get("kind") not in {"month", "interval"}:
            raise ValueError("invalid market period")
        if period["kind"] == "month":
            month = start.isoformat()[:7]
            if period != month_period(month) or row.get("month", month) != month:
                raise ValueError("monthly observations require one full calendar month")
            row["month"] = month
        else:
            row["month"] = None
        if row["value"] is not None:
            value = decimal_value(row["value"])
            if value < 0:
                raise ValueError("market observations must be nonnegative")
            row.update(value=number(value), value_decimal=str(value))
        else:
            row.pop("value_decimal", None)
        if "value_decimal" in original and row["value"] is not None and decimal_value(original["value_decimal"]) != decimal_value(original["value"]):
            raise ValueError("value_decimal differs from value")
        if "rounding_step" in row and decimal_value(row["rounding_step"]) < 0:
            raise ValueError("rounding_step must be nonnegative")
        limits = row.get("scope_limitations", [])
        if not isinstance(limits, list) or any(not isinstance(n, str) for n in limits):
            raise ValueError("scope_limitations must be a text array")
        row.update(scope_signature=scope_signature(row), scope_note=row.get("scope_note", ""),
                   scope_limitations=list(dict.fromkeys(limits)), estimated=True)
        output.append(row)
    return output


def calculate_market(payload):
    records = normalize_records(payload["records"])
    window = payload["window"]
    periods = months(**window)
    if window != {"start": month_period(periods[0])["start"], "end": month_period(periods[-1])["end"]}:
        raise ValueError("market window must contain complete calendar months")
    prior = [f"{int(m[:4])-1:04d}{m[4:]}" for m in periods] if payload.get("yoy", False) else []
    keys = sorted({series_key(r) for r in records})
    cells, previous = [], []
    for key in keys:
        for month in periods + prior:
            matching = [r for r in records if series_key(r) == key and r["month"] == month]
            # A null observation is explicitly missing, never a zero or a revision choice.
            usable = [r for r in matching if r["value"] is not None]
            cell = select_cell((*key[:4], month, key[4]), usable, month in periods)
            if not usable and matching:
                cell["evidence_ids"] = list(dict.fromkeys(e for r in matching for e in r["evidence_ids"]))
                cell["scope_limitations"] = list(dict.fromkeys(n for r in matching for n in r["scope_limitations"]))
            (cells if month in periods else previous).append(cell)
    intervals = [r for r in records if r["period"]["kind"] == "interval" and r["value"] is not None]
    totals = source_totals(cells + previous, periods, prior, intervals)
    consistency = []
    # Compare only this calculation's native intervals with its monthly observations.
    for total in totals:
        checks = [(cells, periods, total["period"], True)]
        if prior:
            checks.append((previous, prior, {"kind": "interval", "start": month_period(prior[0])["start"],
                                            "end": month_period(prior[-1])["end"]}, False))
        for monthly, wanted, period, current in checks:
            native = [r for r in intervals if series_key(r) == series_key(total) and r["period"] == period]
            if not native:
                continue
            direct = select_cell((*series_key(total)[:4], wanted[0], total["metric"]), native, False)
            selected = [r for r in monthly if series_key(r) == series_key(total)]
            complete = (len(selected) == len(wanted) and all(r["value"] is not None for r in selected)
                        and len({r["scope_signature"] for r in selected}) == 1)
            mismatch = direct["value"] is None
            if complete and direct["value"] is not None:
                delta = sum((decimal_value(r["value_decimal"]) for r in selected), decimal_value(0)) - decimal_value(direct["value"])
                allowed = (sum((decimal_value(r.get("rounding_step", 0))/2 for r in selected), decimal_value(0))
                           + decimal_value(direct.get("rounding_step", 0))/2)
                same_scope = direct["scope_signature"] == selected[0]["scope_signature"]
                mismatch = not same_scope or abs(delta) > allowed
                consistency.append({**dict(zip(FIELDS, series_key(total))), "period": period,
                                    "difference": number(delta), "allowed_rounding": number(allowed),
                                    "status": "conflict" if mismatch else "consistent" if delta == 0 else "within_display_rounding",
                                    "evidence_ids": list(dict.fromkeys([e for r in selected for e in r["evidence_ids"]] + direct["evidence_ids"]))})
            if mismatch:
                total.update(yoy=None, yoy_status="not_comparable", reason="月度合计与原生区间值或范围不一致",
                             evidence_ids=list(dict.fromkeys(total["evidence_ids"] + direct["evidence_ids"])))
                if current:
                    total.update(value=None, status="conflict")
    monthly_yoy = [yoy_pair(c, p) for c, p in zip(cells, previous)] if prior else []
    result = {"cells": cells, "totals": totals, "interval_observations": intervals,
              "yoy": {"status": "not_requested" if not prior else
                      "available" if monthly_yoy and all(r["status"] == "available" for r in monthly_yoy) else "partial",
                      "monthly": monthly_yoy},
              "trends": [], "derived": []}
    result["trends"] = trends(cells, {}, result)
    # calculations may include contribution: paired product and category periods, same metric.
    result["derived"] = derive(payload.get("calculations", []), {"metrics": records}, result)
    result["window"] = window
    result["input_records"] = records
    result["consistency_checks"] = consistency
    return result

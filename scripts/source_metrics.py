"""Sensor Tower interval totals and time-series trends."""

from common import METRIC_SOURCE
from scopes import decimal_value, month_period, number


FIELDS = ("entity_type", "entity_id", "country", "platform", "metric")


def select_cell(key, rows, required=True):
    cell = dict(zip(("entity_type", "entity_id", "country", "platform", "month", "metric"), key))
    cell.update(value=None, status="missing", required=required, evidence_ids=[], scope_limitations=[],
                selected_source_group=METRIC_SOURCE, source_groups=[METRIC_SOURCE],
                period=month_period(cell["month"]), reason="没有匹配地区、商店、月份的可用估算数据")
    if not rows:
        return cell
    if any(r["source_group"] != METRIC_SOURCE for r in rows):
        raise ValueError("market metrics require Sensor Tower")
    cell["evidence_ids"] = list(dict.fromkeys(e for r in rows for e in r["evidence_ids"]))
    values = [decimal_value(r.get("value_decimal", r["value"])) for r in rows]
    cell["evidence_ids"] = list(dict.fromkeys(e for r in rows for e in r["evidence_ids"]))
    if len({r["scope_signature"] for r in rows}) != 1 or len(set(values)) > 1:
        cell.update(status="conflict", reason="同源同格有不同范围或数值；保留观察，未任选、平均或取最大值", observations=rows)
        return cell
    selected = rows[0]
    if len(rows) > 1:
        cell["observations"] = rows
    cell.update({k: selected[k] for k in ("value", "value_decimal", "unit", "scope_signature", "scope_note", "geography", "store_scope", "period")})
    for k in ("revenue_basis", "download_basis", "aggregation", "rounding_step"):
        if k in selected:
            cell[k] = selected[k]
    for k in ("category_scope", "coverage", "grain", "identity_scope", "category_relation", "metric_definition"):
        if k in selected:
            cell[k] = selected[k]
    cell.update(estimated=True, selected_source_group=selected["source_group"],
                source_groups=sorted({r["source_group"] for r in rows}),
                scope_limitations=list(selected["scope_limitations"]),
                status="available", reason="Sensor Tower估算；按显式输入核算")
    return cell


def yoy_pair(current, previous):
    row = {k: current[k] for k in ("entity_type", "entity_id", "country", "platform", "month", "metric", "required")}
    row.update(value=None, unit="percent", estimated=True, current_period=current["period"],
               comparison_period=previous["period"], evidence_ids=list(dict.fromkeys(current["evidence_ids"] + previous["evidence_ids"])),
               scope_limitations=list(dict.fromkeys(current["scope_limitations"] + previous["scope_limitations"])))
    if current["value"] is None or previous["value"] is None:
        row.update(status="missing", reason="缺本期或上年同月可用数据")
    elif current["scope_signature"] != previous["scope_signature"] or current["selected_source_group"] != previous["selected_source_group"]:
        row.update(status="not_comparable", reason="本期与上年同期的来源或统计范围不一致")
    elif decimal_value(previous.get("value_decimal", previous["value"])) == 0:
        row.update(status="undefined_zero_base", reason="上年同期为零，不能计算百分比增长")
    else:
        a = decimal_value(current.get("value_decimal", current["value"]))
        b = decimal_value(previous.get("value_decimal", previous["value"]))
        value = (a - b) / b * 100
        row.update(value=number(value), value_decimal=str(value), scope_signature=current["scope_signature"],
                   geography=current["geography"], store_scope=current["store_scope"],
                   source_groups=current["source_groups"],
                   status="available", reason="同来源、同范围的上年同月比较")
    return row


def series_key(row):
    return tuple(row[k] for k in FIELDS)


def trends(cells, normal, calculated):
    from report_values import derive
    results = []
    for key in sorted({series_key(c) for c in cells}):
        selected = [(i, c) for i, c in enumerate(cells) if series_key(c) == key]
        item = {**dict(zip(FIELDS, key)), "id": "trend_" + str(len(results)+1), "unit": "trend", "value": "月度序列不完整，暂不判断完整窗口趋势",
                "status": "partial", "evidence_ids": list(dict.fromkeys(e for _, c in selected for e in c["evidence_ids"])), "scope_limitations": []}
        if len(selected) >= 2 and all(c["value"] is not None for _, c in selected):
            spec = {"id": item["id"], "operation": "trend", "inputs": [{"json_pointer": f"/cells/{i}"} for i, _ in selected]}
            try:
                item.update(derive([spec], normal, calculated)[0])
                ordered = sorted((c for _, c in selected), key=lambda c: c["month"])
                rising = [b["month"] for a, b in zip(ordered, ordered[1:]) if decimal_value(b["value_decimal"]) > decimal_value(a["value_decimal"])]
                falling = [b["month"] for a, b in zip(ordered, ordered[1:]) if decimal_value(b["value_decimal"]) < decimal_value(a["value_decimal"])]
                item.update(rising_months=rising, falling_months=falling)
                item["value"] += "；上涨月份：" + ("、".join(rising) or "无") + "；下跌月份：" + ("、".join(falling) or "无")
                item["value"] += "；峰值：" + item["peak_period"]["start"][:7] + "；谷值：" + item["trough_period"]["start"][:7]
            except ValueError as exc:
                item["reason"] = str(exc)
        results.append(item)
    return results


def source_totals(cells, periods, previous_periods, intervals=()):
    results = []
    groups = {(series_key(c), c["selected_source_group"]) for c in cells}
    window = {"kind": "interval", "start": month_period(periods[0])["start"], "end": month_period(periods[-1])["end"]}
    prior_window = {"kind": "interval", "start": month_period(previous_periods[0])["start"], "end": month_period(previous_periods[-1])["end"]} if previous_periods else None
    groups |= {(series_key(r), r["source_group"]) for r in intervals if r["period"] == window}
    def aggregate(key, source, wanted, period):
        rows = [c for c in cells if series_key(c) == key and c["selected_source_group"] == source and c["month"] in wanted]
        native = [r for r in intervals if series_key(r) == key and r["source_group"] == source and r["period"] == period]
        if len(rows) == len(wanted) and all(c["value"] is not None for c in rows) and len({c["scope_signature"] for c in rows}) == 1:
            total = sum((decimal_value(c["value_decimal"]) for c in rows), decimal_value(0))
            result = {**rows[0], "value": number(total), "value_decimal": str(total), "status": "available", "period": period, "month": None,
                      "scope_limitations": list(dict.fromkeys(n for c in rows for n in c["scope_limitations"])),
                      "evidence_ids": list(dict.fromkeys(e for c in rows for e in c["evidence_ids"])), "calculation": "monthly_sum"}
            return result
        if native:
            result = select_cell((*key[:4], period["start"][:7], key[4]), native, False)
            result.update(period=period, month=None, calculation="source_interval", selected_source_group=source)
            return result
        return None
    for key, source in sorted(groups):
        record = {**dict(zip(FIELDS, key)), "source_group": source, "value": None, "status": "missing", "yoy": None,
                  "selected_source_group": source, "period": window,
                  "yoy_status": "not_requested" if not previous_periods else "missing", "evidence_ids": [], "scope_limitations": []}
        current = aggregate(key, source, periods, window)
        prior = aggregate(key, source, previous_periods, prior_window) if previous_periods else None
        if current:
            record.update(current, source_group=source)
        if current and prior and current["value"] is not None and prior["value"] is not None:
            if current["scope_signature"] != prior["scope_signature"]:
                record["yoy_status"] = "not_comparable"
            else:
                baseline = decimal_value(prior["value_decimal"])
                growth = (decimal_value(current["value_decimal"]) - baseline) / baseline * 100 if baseline else None
                record.update(yoy=number(growth) if growth is not None else None,
                              yoy_status="available" if baseline else "undefined_zero_base",
                              comparison_period=prior["period"], yoy_evidence_ids=list(dict.fromkeys(current["evidence_ids"] + prior["evidence_ids"])),
                              yoy_scope_limitations=list(dict.fromkeys(current["scope_limitations"] + prior["scope_limitations"])))
        results.append(record)
    return results

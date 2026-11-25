"""Small fixed calculations and report bindings; no arbitrary expressions or LLM."""
import re
from common import ident
from scopes import decimal_value, number
from tool_results import pointer_value


def resolve(reference, normal, calculated):
    dataset = reference.get("dataset", "calculated")
    pointer = reference["json_pointer"]
    pools = {"normalized": (normal, {"metrics"}),
             "calculated": (calculated, {"cells", "totals", "interval_observations", "yoy", "derived", "trends", "observation_stats"})}
    if dataset not in pools or len(pointer.split("/")) < 3 or pointer.split("/")[1] not in pools[dataset][1]:
        raise ValueError("metric_ref must select a normalized/calculated metric record")
    record = pointer_value(pools[dataset][0], pointer)
    if not isinstance(record, dict) or "value" not in record or (record["value"] is not None and not record.get("evidence_ids")):
        raise ValueError("metric_ref must select a whole evidence-bound record")
    if dataset == "normalized":
        identity = ("entity_type", "entity_id", "country", "platform", "metric", "period")
        resolved = [r for r in calculated.get("cells", []) + calculated.get("totals", [])
                    if all(r.get(k) == record.get(k) for k in identity)]
        if any(r.get("status") in {"conflict"} for r in resolved):
            raise ValueError("conflicting metrics cannot be inserted as report values")
    if record.get("status") in {"conflict"}:
        raise ValueError("conflicting metrics cannot be inserted as report values")
    if any(record.get(k) != v for k, v in reference.get("match", {}).items()):
        raise ValueError("metric_ref record no longer matches its expected identity/scope")
    if reference.get("field", "value") == "yoy":
        if record.get("yoy_status") != "available" or record.get("yoy") is None:
            raise ValueError("yoy display requires an available percent, not the absolute amount")
        record = {**record, "value": record.get("yoy"), "unit": "percent", "status": record.get("yoy_status", "missing"),
                  "evidence_ids": record.get("yoy_evidence_ids", []), "scope_limitations": record.get("yoy_scope_limitations", [])}
        record.pop("value_decimal", None)
    elif reference.get("field", "value") != "value":
        raise ValueError("unsupported metric_ref field")
    return record


def _finish_derived(out, value):
    out["value"] = value if isinstance(value, str) or value is None else number(value)
    if value is None:
        out["status"] = "undefined_zero_base"
    elif not isinstance(value, str):
        out["value_decimal"] = str(value)
    return out


def contribution_records(spec, rows):
    """Product deltas, category net change, shares, and the unlisted remainder."""
    grouped = {"market_current": [], "market_baseline": [], "entity_current": [], "entity_baseline": []}
    for ref, row in zip(spec["inputs"], rows, strict=True):
        role = ref.get("role")
        if role not in grouped:
            raise ValueError("contribution input requires role market_current, market_baseline, entity_current or entity_baseline")
        grouped[role].append(row)
    if len(grouped["market_current"]) != 1 or len(grouped["market_baseline"]) != 1 or not grouped["entity_current"]:
        raise ValueError("contribution requires one market pair and at least one product pair")
    currents = {row["entity_id"]: row for row in grouped["entity_current"]}
    baselines = {row["entity_id"]: row for row in grouped["entity_baseline"]}
    if len(currents) != len(grouped["entity_current"]) or len(baselines) != len(grouped["entity_baseline"]) or set(currents) != set(baselines):
        raise ValueError("contribution entity periods are not paired")
    if len({row.get("scope_signature") for row in rows}) != 1 or len({row.get("metric") for row in rows}) != 1 or len({row.get("unit") for row in rows}) != 1:
        raise ValueError("contribution requires one metric and definition")
    unit = rows[0]["unit"]
    if unit != "downloads" and not re.fullmatch(r"[A-Z]{3}", unit or ""):
        raise ValueError("contribution supports downloads or currency amounts")

    def period_of(group):
        periods = [row["period"] for row in group]
        if any(period != periods[0] for period in periods):
            raise ValueError("contribution periods do not align")
        return periods[0]

    current_period = period_of(grouped["market_current"] + grouped["entity_current"])
    baseline_period = period_of(grouped["market_baseline"] + grouped["entity_baseline"])
    if current_period["start"] <= baseline_period["end"]:
        raise ValueError("contribution current period must follow the baseline period")
    market_now = decimal_value(grouped["market_current"][0]["value"])
    market_then = decimal_value(grouped["market_baseline"][0]["value"])
    market_delta = market_now - market_then
    near = decimal_value(spec.get("near_zero", 0))
    if near < 0:
        raise ValueError("near_zero cannot be negative")
    shared_keys = ("country", "platform", "metric", "geography", "store_scope", "selected_source_group", "source_group",
                   "scope_signature", "category_scope", "identity_scope", "grain", "coverage", "revenue_basis",
                   "download_basis", "metric_definition")

    def shell(row, record_id, operation):
        ident(record_id)
        out = {key: row[key] for key in shared_keys if key in row}
        out.update(id=record_id, operation=operation, contribution_group=spec["id"], inputs=spec["inputs"],
                   estimated=True, status="available", unit=unit, period=current_period, baseline_period=baseline_period,
                   evidence_ids=list(dict.fromkeys(item for source in rows for item in source["evidence_ids"])),
                   scope_limitations=list(dict.fromkeys(note for source in rows for note in source.get("scope_limitations", []))))
        return out

    records = []
    market_row = grouped["market_current"][0]
    market_out = shell(market_row, f'{spec["id"]}_market', "contribution_market")
    market_out.update(entity_type=market_row.get("entity_type"), entity_id=market_row.get("entity_id"),
                      current_value=number(market_now), baseline_value=number(market_then))
    records.append(_finish_derived(market_out, market_delta))
    entity_deltas = []
    for index, current in enumerate(grouped["entity_current"]):
        prior = baselines[current["entity_id"]]
        delta = decimal_value(current["value"]) - decimal_value(prior["value"])
        entity_deltas.append(delta)
        delta_out = shell(current, f'{spec["id"]}_d{index}', "contribution_delta")
        delta_out.update(entity_type=current.get("entity_type"), entity_id=current["entity_id"],
                         current_value=number(decimal_value(current["value"])), baseline_value=number(decimal_value(prior["value"])))
        records.append(_finish_derived(delta_out, delta))
        share_out = shell(current, f'{spec["id"]}_s{index}', "contribution_share")
        share_out.update(entity_type=current.get("entity_type"), entity_id=current["entity_id"], unit="percent")
        share = None if abs(market_delta) <= near else delta / market_delta * 100
        records.append(_finish_derived(share_out, share))
    residual = market_delta - sum(entity_deltas, decimal_value(0))
    residual_out = shell(market_row, f'{spec["id"]}_residual', "contribution_residual")
    residual_out.update(entity_type="remainder", entity_id="unlisted", label="未列入产品的合计")
    records.append(_finish_derived(residual_out, residual))
    return records


def derive(specs, normal, calculated):
    results, ids = [], set()
    for spec in specs:
        ident(spec["id"])
        if spec["id"] in ids:
            raise ValueError("duplicate derived metric ID")
        ids.add(spec["id"])
        op = spec["operation"]
        if op not in {"sum", "ratio", "percent_change", "concentration", "trend", "contribution"}:
            raise ValueError("unsupported fixed calculation")
        # Only earlier derived records may be referenced: no dependency planner.
        calculated["derived"] = results
        rows = [resolve(r, normal, calculated) for r in spec["inputs"]]
        if not rows or any(r["value"] is None for r in rows):
            raise ValueError("derived calculation requires available inputs")
        if len({r.get("selected_source_group", r.get("source_group")) for r in rows}) != 1:
            raise ValueError("derived calculation cannot combine providers")
        dimensions = {(r.get("country"), r.get("platform"),
                       tuple(sorted(r.get("geography", {}).get("countries", []))),
                       tuple(sorted(r.get("store_scope", {}).get("stores", [])))) for r in rows}
        if len(dimensions) != 1:
            raise ValueError("derived calculation requires aligned region/store scope")
        if op == "contribution":
            produced = contribution_records(spec, rows)
            for record in produced:
                if record["id"] in ids:
                    raise ValueError("duplicate derived metric ID")
                ids.add(record["id"])
            results.extend(produced)
            continue
        amounts = [decimal_value(r.get("value_decimal", r["value"])) for r in rows]
        out = {k: rows[0][k] for k in ("entity_type", "entity_id", "country", "platform", "metric", "unit", "geography", "store_scope", "period", "selected_source_group", "source_group", "scope_signature", "category_scope", "identity_scope", "grain", "coverage", "revenue_basis", "download_basis", "metric_definition") if k in rows[0]}
        out.update(id=spec["id"], operation=op, inputs=spec["inputs"], estimated=True, status="available",
                   evidence_ids=list(dict.fromkeys(e for r in rows for e in r["evidence_ids"])),
                   scope_limitations=list(dict.fromkeys(n for r in rows for n in r.get("scope_limitations", []))))
        if op in {"sum", "percent_change", "trend"}:
            if len({(r.get("scope_signature"), r.get("entity_id"), r.get("metric"), r.get("unit")) for r in rows}) != 1:
                raise ValueError("time calculations require one entity/metric/definition")
            periods = sorted((r["period"] for r in rows), key=lambda p: p["start"])
            if any(a["end"] >= b["start"] for a, b in zip(periods, periods[1:])):
                raise ValueError("time calculations cannot sum overlapping observations")
        if op == "sum":
            unit = rows[0]["unit"]
            if unit != "downloads" and not re.fullmatch(r"[A-Z]{3}", unit):
                raise ValueError("sum supports additive downloads/currency amounts, not rates or ratios")
            value = sum(amounts, decimal_value(0))
            out["period"] = {"kind": "interval", "start": periods[0]["start"], "end": periods[-1]["end"]}
        elif op == "percent_change":
            if len(rows) != 2:
                raise ValueError("percent_change takes current, baseline")
            value = (amounts[0] - amounts[1]) / amounts[1] * 100 if amounts[1] else None
            out["unit"] = "percent"
        elif op == "ratio":
            if len(rows) != 2 or rows[0]["period"] != rows[1]["period"] or rows[0]["entity_id"] != rows[1]["entity_id"]:
                raise ValueError("ratio takes two aligned records for the same entity")
            value = amounts[0] / amounts[1] if amounts[1] else None
            out["unit"] = rows[0]["unit"] + "/" + rows[1]["unit"]
        elif op == "concentration":
            denominator = rows[-1]
            numerators = rows[:-1]
            if denominator.get("unit") != "downloads" and not re.fullmatch(r"[A-Z]{3}", denominator.get("unit", "")):
                raise ValueError("concentration requires additive measured amounts, not rates or ratios")
            if not numerators:
                raise ValueError("concentration requires numerator records and a denominator")
            if any(r["period"] != denominator["period"] or r["metric"] != denominator["metric"]
                   or r["unit"] != denominator["unit"] for r in numerators):
                raise ValueError("concentration requires aligned periods, metrics and units")
            identities = [(r.get("entity_type"), r["entity_id"]) for r in numerators]
            if len(set(identities)) != len(identities):
                raise ValueError("concentration contains duplicate numerator records")
            basis = "revenue_basis" if denominator["metric"] == "revenue" else "download_basis"
            if any(r.get(basis) != denominator.get(basis)
                   or r.get("metric_definition", {}) != denominator.get("metric_definition", {}) for r in numerators):
                raise ValueError("concentration inputs declare different measurement definitions")
            value = sum(amounts[:-1], decimal_value(0)) / amounts[-1] * 100 if amounts[-1] else None
            out["unit"] = "percent"
        else:
            ordered = sorted(zip(rows, amounts), key=lambda x: x[0]["period"]["start"])
            changes = [b[1] - a[1] for a, b in zip(ordered, ordered[1:])]
            if not changes:
                raise ValueError("trend requires at least two observations")
            direction = "strictly_increasing" if all(d > 0 for d in changes) else "strictly_decreasing" if all(d < 0 for d in changes) else "flat" if all(d == 0 for d in changes) else "nondecreasing" if all(d >= 0 for d in changes) else "nonincreasing" if all(d <= 0 for d in changes) else "mixed"
            value = {"strictly_increasing": "逐期上升", "strictly_decreasing": "逐期下降", "flat": "持平", "nondecreasing": "整体不降（含持平）", "nonincreasing": "整体不升（含持平）", "mixed": "期间有升有降"}[direction]
            out.update(direction=direction, unit="trend", peak_period=max(ordered, key=lambda x: x[1])[0]["period"], trough_period=min(ordered, key=lambda x: x[1])[0]["period"])
        out["value"] = value if isinstance(value, str) or value is None else number(value)
        if value is None:
            out["status"] = "undefined_zero_base"
        elif not isinstance(value, str):
            out["value_decimal"] = str(value)
        results.append(out)
    return results


def display(reference, normal, calculated):
    record = resolve(reference, normal, calculated)
    if record["value"] is None:
        return "不可计算", record
    if isinstance(record["value"], str):
        return record["value"], record
    digits = reference.get("digits", 2)
    divisor = decimal_value(reference.get("divisor", 1))
    if not isinstance(digits, int) or isinstance(digits, bool) or not 0 <= digits <= 6 or divisor <= 0:
        raise ValueError("display digits must be 0..6 and divisor positive")
    value = decimal_value(record.get("value_decimal", record["value"])) / divisor
    suffix = reference.get("suffix", "")
    if reference.get("field") == "yoy":
        if record.get("unit") != "percent":
            raise ValueError("yoy display requires a percent")
        suffix = suffix or "%"
        if "%" not in suffix or any(token in suffix for token in ("美元", "次", "百万")):
            raise ValueError("yoy display cannot use an absolute amount suffix")
    return f"{value:,.{digits}f}" + suffix, record

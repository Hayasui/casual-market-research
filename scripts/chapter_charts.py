"""Offline chart components for market and case chapters."""
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from unicodedata import east_asian_width

from common import digest, ident, months
from report_view import E, chart_svg, label, members_text, short_number
from scopes import decimal_value


COLORS = ("#167666", "#ae5845", "#375c96", "#7a548e", "#946012", "#526a38")


def _nice_limit(raw_max, ticks=5):
    """Round a nonnegative maximum up to a whole number of nice steps."""
    raw_max = raw_max or 1
    magnitude = 10 ** math.floor(math.log10(raw_max / ticks))
    return next(step * magnitude * ticks for step in (1, 2, 2.5, 5, 10)
                if step * magnitude * ticks >= raw_max)


def clean_item_name(text: str, max_len: int) -> str:
    name = text.strip()
    if len(name) <= max_len:
        return name
    name = re.split(r"[:：]| [-–] ", name, maxsplit=1)[0].strip() or name
    if len(name) <= max_len:
        return name
    if max_len <= 0:
        return ""
    limit = max_len - 1
    prefix = name[:limit]
    if name[limit].isspace() or prefix[-1:].isspace():
        return prefix.rstrip() + "…"
    words = prefix.rsplit(maxsplit=1)
    if len(words) > 1:
        return words[0] + "…"
    for match in re.finditer(r"[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*", name):
        if match.start() < limit < match.end():
            prefix = name[:match.start()]
            break
    return prefix.rstrip() + "…"


def plot(title, categories, traces, units, *, limits=None, events=()):
    """Draw precomputed values. None leaves a gap; markers use the shared x axis."""
    legend_traces = list(enumerate(traces))
    axis_units = units
    if len(units) == 2:
        legend_traces.sort(key=lambda item: item[1]["axis"])
        axis_units = ("净收入（USD）", "下载量（次）")
    legend_gap = 22
    legend = []
    for index, trace in legend_traces:
        label_width = sum(13 if east_asian_width(char) in "WF" else 8 for char in trace["label"])
        legend.append((trace, COLORS[index % len(COLORS)], 24 + label_width))
    legend_width = sum(item_width for _, _, item_width in legend) + legend_gap * max(0, len(legend) - 1)
    canvas_width = max(960, legend_width + 40)
    left, right, top, bottom = 72, canvas_width - 74, 88, 300
    width = (right - left) / len(categories)
    height = 374
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" height="{height}" viewBox="0 0 {canvas_width} {height}" role="img">',
              f'<title>{E(title)}</title><rect width="{canvas_width}" height="{height}" fill="white"/>',
              '<g font-family="Segoe UI,Microsoft YaHei,sans-serif" fill="#253b37">']
    maxima = limits or [_nice_limit(max((v for t in traces if t["axis"] == axis
                                        for v in t["values"].values() if v is not None), default=0))
                        for axis in range(len(units))]

    def y(value, axis):
        return bottom - (bottom - top) * value / maxima[axis]

    for i in range(6):
        yy = bottom - (bottom - top) * i / 5
        pieces.append(f'<path d="M{left} {yy}H{right}" stroke="#e0e8e4"/>')
        for axis, maximum in enumerate(maxima):
            xx, align = (left - 8, "end") if axis == 0 else (right + 8, "start")
            tick = maximum * i / 5
            tick_text = f"{tick:g}" if 0 < tick < 1 else short_number(tick)
            pieces.append(f'<text x="{xx}" y="{yy+4}" text-anchor="{align}" font-size="12">{tick_text}</text>')
    for axis, unit in enumerate(axis_units):
        xx, align = (left, "start") if axis == 0 else (right, "end")
        pieces.append(f'<text x="{xx}" y="{top - 32}" text-anchor="{align}" font-size="13">{E(unit)}</text>')

    positions = {key: left + (i + .5) * width for i, key in enumerate(categories)}
    for i, key in enumerate(categories):
        if i % max(1, (len(categories)+9)//10) == 0:
            pieces.append(f'<text x="{positions[key]:.2f}" y="323" text-anchor="middle" font-size="12">{E(key)}</text>')
    bars = [t for t in traces if t["bar"]]
    bar_width = min(52, width * .72 / max(1, len(bars)))
    for n, trace in enumerate(traces):
        color, axis = COLORS[n % len(COLORS)], trace["axis"]
        previous = None
        for key, x in positions.items():
            value = trace["values"].get(key)
            if value is None:
                previous = None
                continue
            yy = y(value, axis)
            tooltip = f'{key} · {trace["label"]}：{value:,.2f} {units[axis]}'
            if key in trace.get("details", {}):
                tooltip += " · " + trace["details"][key]
            if trace["bar"]:
                xx = x + (bars.index(trace) - len(bars)/2) * bar_width
                pieces.append(f'<rect x="{xx:.2f}" y="{yy:.2f}" width="{bar_width:.2f}" height="{bottom-yy:.2f}" fill="{color}"><title>{E(tooltip)}</title></rect>')
            else:
                if previous:
                    px, py = previous
                    pieces.append(f'<path d="M{px:.2f} {py:.2f}L{x:.2f} {yy:.2f}" stroke="{color}" stroke-width="2.5"/>')
                pieces.append(f'<circle cx="{x:.2f}" cy="{yy:.2f}" r="4" fill="{color}"><title>{E(tooltip)}</title></circle>')
                previous = (x, yy)
    for n, event in enumerate(events, 1):
        x = positions[event["month"]]
        pieces.append(f'<path d="M{x:.2f} {top}V{bottom}" stroke="#956e45" stroke-dasharray="4 4"/>'
                      f'<circle cx="{x:.2f}" cy="72" r="11" fill="#956e45"/>'
                      f'<text x="{x:.2f}" y="76" fill="white" text-anchor="middle" font-size="12">{n}</text>')
    legend_x, legend_y = (canvas_width - legend_width) / 2, height - 22
    for trace, color, item_width in legend:
        if trace["bar"]:
            pieces.append(f'<rect x="{legend_x:.2f}" y="{legend_y - 6}" width="12" height="12" fill="{color}"/>')
        else:
            pieces.append(f'<line x1="{legend_x:.2f}" y1="{legend_y}" x2="{legend_x + 18:.2f}" y2="{legend_y}" stroke="{color}" stroke-width="2.5"/>'
                          f'<circle cx="{legend_x + 9:.2f}" cy="{legend_y}" r="3.5" fill="{color}"/>')
        pieces.append(f'<text x="{legend_x + 24:.2f}" y="{legend_y + 4}" font-size="13">{E(trace["label"])}</text>')
        legend_x += item_width + legend_gap
    return ("".join(pieces) + "</g></svg>").encode()


def market_chart(spec, metrics, labels=None):
    selectors = spec["selectors"]
    kind = spec["kind"]
    keys = {"entity_type", "entity_id", "country", "platform", "metric"}
    if not selectors or any(set(s) != keys for s in selectors):
        raise ValueError("market chart requires complete selectors")
    if len({(s["country"], s["platform"]) for s in selectors}) != 1:
        raise ValueError("market chart selectors must share region and store")
    if kind == "market_dual_axis":
        if [s["metric"] for s in selectors] != ["revenue", "downloads"] or any(
            selectors[0][key] != selectors[1][key] for key in keys - {"metric"}
        ):
            raise ValueError("dual axis requires revenue then downloads for the same object")
    elif (not 1 <= len(selectors) <= len(COLORS)
          or any(s["entity_type"] != "product" for s in selectors)
          or len({s["entity_id"] for s in selectors}) != len(selectors)
          or len({s["metric"] for s in selectors}) != 1):
        raise ValueError("multi-product chart requires 1–6 distinct products and one metric")
    names = labels or {}
    traces, picked, units = [], [], []
    chart_months = sorted({c["month"] for c in metrics["cells"] if any(
        all(c.get(k) == v for k, v in s.items()) for s in selectors
    )})
    for n, selector in enumerate(selectors):
        cells = [c for c in metrics["cells"] if all(c.get(k) == v for k, v in selector.items())
                 and c["value"] is not None]
        if len({c["month"] for c in cells}) != len(cells):
            raise ValueError("chart contains duplicate series months")
        if not cells or len({c["scope_signature"] for c in cells}) != 1:
            return None
        if any(c.get("selected_source_group") != "Sensor Tower" or c["value"] < 0 for c in cells):
            raise ValueError("market chart requires nonnegative Sensor Tower values")
        metric = selector["metric"]
        unit = label(cells[0]["unit"])
        axis = n if kind == "market_dual_axis" else 0
        traces.append(dict(label=f'{label(metric)}（{"柱" if axis == 0 else "线"}）' if kind == "market_dual_axis"
                           else names.get(selector["entity_id"], selector["entity_id"]),
                           axis=axis, bar=kind == "market_dual_axis" and axis == 0,
                           values={c["month"]: c["value"] for c in cells}))
        units.append(unit)
        picked.extend(cells)
    regions = {tuple(sorted(c.get("geography", {}).get("countries", []))) for c in picked}
    stores = {tuple(sorted(c.get("store_scope", {}).get("stores", []))) for c in picked}
    if len(regions) != 1 or len(stores) != 1 or (
        kind == "market_trend" and len({c["scope_signature"] for c in picked}) != 1
    ):
        raise ValueError("market chart series have incompatible scopes or metric definitions")
    chart_months = months(chart_months[0] + "-01", chart_months[-1] + "-01")
    events = sorted(spec.get("events", []), key=lambda event: event["month"])
    if len({e["month"] for e in events}) != len(events) or any(
        not re.fullmatch(r"\d{4}-\d{2}", e["month"]) or e["month"] not in chart_months
        or not e["text"].strip() or not e.get("evidence_ids") for e in events
    ):
        raise ValueError("chart events require a unique chart month, explanation and evidence")
    first = picked[0]
    basis = "；".join(dict.fromkeys(f'{label(c["metric"])}定义：{label(c.get("revenue_basis" if c["metric"] == "revenue" else "download_basis"))}' for c in picked))
    caption = ("Sensor Tower 估算 · " + members_text(first.get("geography"), "countries") + " · "
               + members_text(first.get("store_scope"), "stores") + " · " + chart_months[0] + " 至 " + chart_months[-1]
               + "；" + basis + "。空缺月份留白。")
    return plot(spec["title"], chart_months, traces, units if kind == "market_dual_axis" else units[:1],
                events=events), picked, caption, events


def review_chart(spec, metrics):
    jobs = spec["review_jobs"]
    statistic = {"review_weekly": "weekly_review_average", "review_versions": "version_review_average",
                 "review_themes": "review_theme_share"}[spec["kind"]]
    if statistic == "version_review_average":
        if len(jobs) != 1:
            raise ValueError("version charts accept one review job")
    elif not 1 <= len(jobs) <= 6 or len(set(jobs)) != len(jobs):
        raise ValueError("weekly and theme charts accept 1–6 review jobs")
    records = metrics.get("observation_stats", [])
    bases = {r["id"]: r for r in records if r["kind"] == "reviews" and r["statistic"] == "sample_size"}
    if any(j not in bases for j in jobs):
        raise ValueError("review charts require explicit review results")
    same_product = len({bases[j]["entity_id"] for j in jobs}) == 1
    if statistic == "weekly_review_average" and same_product and len(jobs) > 1 and len({bases[j]["scope"]["store"] for j in jobs}) != len(jobs):
        raise ValueError("weekly comparison requires distinct stores")
    grouped = defaultdict(list)
    for r in records:
        job = r.get("job_id", r["id"].rsplit("_", 1)[0])
        if job in jobs and r["statistic"] == statistic and not r.get("conflicting_ids"):
            grouped[job].append(r)
    traces, categories, picked = [], [], []
    for job in jobs:
        rows = grouped[job]
        if not any(r["value"] is not None for r in rows):
            return None
        picked.extend(rows)
        groups = ("positive", "negative") if statistic == "review_theme_share" else (None,)
        key = {"weekly_review_average": "week_start", "version_review_average": "version",
               "review_theme_share": "theme"}[statistic]
        for group in groups:
            selected = [r for r in rows if r.get("polarity") == group]
            polarity = {"positive": "4–5 星组", "negative": "1–2 星组"}.get(group)
            product = bases[job]["entity_id"]
            name = polarity or label(bases[job]["scope"]["store"])
            if polarity and len({bases[j]["entity_id"] for j in jobs}) > 1:
                name = f"{product} · {polarity}"
            details = {}
            for r in selected:
                category = r[key] if len(jobs) == 1 or not group else f'{product} · {r[key]}'
                details[category] = (f'{r["count"]}/{r["denominator"]}' if group else
                                     f'n={r["sample_size"]}' + ("，不完整周" if r.get("partial_week") else ""))
            values = {}
            for r in selected:
                category = r[key] if len(jobs) == 1 or not group else f'{product} · {r[key]}'
                values[category] = r["value"]
            traces.append(dict(label=name, axis=0, bar=statistic != "weekly_review_average",
                               values=values, details=details))
            categories.extend(values)
    categories = sorted(set(categories)) if statistic == "weekly_review_average" else list(dict.fromkeys(categories))
    if statistic == "weekly_review_average":
        start, end = date.fromisoformat(categories[0]), date.fromisoformat(categories[-1])
        categories = [(start + timedelta(days=7*i)).isoformat() for i in range((end-start).days//7+1)]
    notes = [f'{label(bases[j]["scope"]["store"])} · {bases[j]["actual_start"]} 至 {bases[j]["actual_end"]}'
             f' · 样本 {bases[j]["value"]} 条 · 地区 {bases[j]["scope"]["countries"]} · 筛选 {bases[j]["scope"]["filters"]}' for j in jobs]
    if statistic == "weekly_review_average":
        partial = sorted({r["week_start"] for r in picked if r.get("partial_week")})
        if partial:
            notes.append("不完整周（周一日期）：" + "、".join(partial))
        if any(r.get("partial_week") is None for r in picked):
            notes.append("部分样本的查询窗口未知，无法判断首尾周是否完整")
    if statistic == "version_review_average":
        missing = next((r["value"] for r in records if r.get("job_id", r["id"].rsplit("_", 1)[0]) == jobs[0]
                        and r["statistic"] == "reviews_without_version"), None)
        notes.append(f"版本缺失 {missing} 条，未纳入版本均分")
        notes.append("各版本样本：" + "、".join(f'{r["version"]} n={r["sample_size"]}' for r in picked))
    if statistic == "review_theme_share":
        notes.append("按标题及正文主题词统计，可多选；各组样本单独计算，不合并成总比率")
        for group, title in (("positive", "4–5 星组"), ("negative", "1–2 星组")):
            denominators = [r["denominator"] for r in picked if r["polarity"] == group]
            notes.append(title + " " + "、".join(f"n={item}" for item in denominators))
    percent = statistic == "review_theme_share"
    return (plot(spec["title"], categories, traces, ["%" if percent else "星"], limits=[100 if percent else 5]),
            picked, "；".join(notes), [])


def _selector_months(selectors, metrics):
    found = sorted({cell["month"] for cell in metrics.get("cells", []) if any(
        all(cell.get(key) == value for key, value in selector.items()) for selector in selectors)})
    if not found:
        return []
    return months(found[0] + "-01", found[-1] + "-01")


def split_market_trend(spec, metrics, labels):
    """Keep confirmation order and split a long trend into charts of at most six products."""
    selectors = spec["selectors"]
    groups = [selectors[index:index + 6] for index in range(0, len(selectors), 6)]
    events = spec.get("events", [])
    parts, covered = [], set()
    for index, group in enumerate(groups, 1):
        part_months = set(_selector_months(group, metrics))
        covered.update(part_months)
        part = {**spec, "selectors": group, "events": [event for event in events if event.get("month") in part_months],
                "title": f'{spec["title"]}（{index}/{len(groups)}）'}
        result = market_chart(part, metrics, labels)
        if not result:
            return None
        blob, rows, caption, part_events = result
        start = (index - 1) * 6 + 1
        note = f"同组拆分：第 {index}/{len(groups)} 张，确认顺序第 {start}–{start + len(group) - 1} 个产品。"
        parts.append((blob, rows, note, caption, part_events))
    if any(event.get("month") not in covered for event in events):
        raise ValueError("chart events require a unique chart month, explanation and evidence")
    return parts


def _sensor_tower(row):
    return row.get("selected_source_group", row.get("source_group")) == "Sensor Tower"


def _match_period(pool, selector, period):
    rows = [row for row in pool if all(row.get(key) == value for key, value in selector.items()) and row.get("value") is not None]
    if period is not None:
        rows = [row for row in rows if row.get("period") == period]
    return rows


def horizontal_rank_bars(title, items, unit, shares=None):
    """Draw ranks in input order, with optional shares expressed as percentages."""
    left, right, row_h = 250, 820, 34
    top = 48
    axis_y = top + row_h * max(1, len(items))
    height = axis_y + 47
    maximum = _nice_limit(max((value for _, value in items), default=0))
    is_usd = "USD" in unit.upper()
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="{height}" viewBox="0 0 960 {height}" role="img">',
              f'<title>{E(title)}</title><rect width="960" height="{height}" fill="white"/>',
              '<g font-family="Segoe UI,Microsoft YaHei,sans-serif" fill="#253b37">',
              f'<text x="{right}" y="30" text-anchor="end" font-size="12">{E(unit)}</text>']
    for tick in range(6):
        x = left + (right - left) * tick / 5
        value = maximum * tick / 5
        tick_text = f"{value:g}" if 0 < value < 1 else short_number(value)
        pieces.append(f'<path d="M{x:.2f} {top}V{axis_y}" stroke="#e0e8e4"/>'
                      f'<text x="{x:.2f}" y="{axis_y + 20}" text-anchor="middle" font-size="12">{tick_text}</text>')
    pieces.append(f'<path d="M{left} {axis_y}H{right}" stroke="#c5d2cc"/>')

    for index, (name, value) in enumerate(items):
        y = top + row_h * index + row_h / 2
        bar_width = (right - left) * (value / maximum)
        display_name = clean_item_name(name, 28)
        name_width = sum(12 if east_asian_width(char) in "WF" else 7 for char in display_name)
        fit = ' textLength="226" lengthAdjust="spacingAndGlyphs"' if name_width > 226 else ""
        amount = short_number(value)
        if is_usd:
            for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
                if value >= scale:
                    amount = f"{value / scale:,.1f}{suffix}"
                    break
            amount = "$" + amount
        if shares is not None and shares[index] is not None:
            amount += f" ({shares[index]:.1f}%)"
        tooltip = f"{name} · {value:,.2f} {unit}"
        pieces.append(f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" font-size="12"{fit}>{E(display_name)}</text>'
                      f'<rect x="{left}" y="{y - 9:.2f}" width="{bar_width:.2f}" height="18" fill="{COLORS[0]}"><title>{E(tooltip)}</title></rect>'
                      f'<text x="{left + bar_width + 8:.2f}" y="{y + 4:.2f}" font-size="12">{E(amount)}</text>')
    return ("".join(pieces) + "</g></svg>").encode()


def rank_chart(spec, metrics, labels):
    selectors = spec.get("selectors") or []
    if not selectors:
        raise ValueError("rank chart requires selectors")
    pool = list(metrics.get("totals", [])) + [row for row in metrics.get("derived", []) if row.get("operation") == "sum"]
    picked = []
    for selector in selectors:
        rows = [row for row in _match_period(pool, selector, spec.get("period")) if row.get("status", "available") == "available"]
        if len(rows) != 1:
            return None
        if not _sensor_tower(rows[0]) or rows[0]["value"] < 0:
            raise ValueError("rank chart requires nonnegative Sensor Tower values")
        picked.append(rows[0])
    if len({row.get("scope_signature") for row in picked}) != 1 or len({row.get("metric") for row in picked}) != 1:
        raise ValueError("rank chart series have incompatible scopes")
    ordered = sorted(picked, key=lambda row: row["value"], reverse=True)
    items, seen = [], set()
    for row in ordered:
        name = (labels or {}).get(row.get("entity_id"), row.get("entity_id"))
        if name in seen:
            name = f"{name}（{row.get('entity_id')}）"
        seen.add(name)
        items.append((name, row["value"]))
    first = picked[0]
    caption = ("Sensor Tower 估算 · 按数值降序 · " + members_text(first.get("geography"), "countries") + " · "
               + members_text(first.get("store_scope"), "stores") + "。")
    denominator = spec.get("denominator")
    shares = None
    if denominator:
        base = _match_period(pool, denominator, spec.get("period"))
        if len(base) == 1 and base[0]["value"]:
            shares = [value / base[0]["value"] * 100 for _, value in items]
            caption += "份额分母为所选品类合计：" + "；".join(
                f"{name} {share:.1f}%" for (name, _), share in zip(items, shares)) + "。"
        elif len(base) == 1:
            caption += "份额分母为零，只保留绝对额。"
    return horizontal_rank_bars(spec["title"], items, label(first.get("unit", "")), shares), ordered, caption, []


def signed_bars(title, items, unit):
    row_h, left, right = 36, 240, 860
    height = 88 + row_h * max(1, len(items))
    span = max([1, *(abs(value) for _, value, _ in items)])
    has_neg = any(value < 0 for _, value, _ in items)
    has_pos = any(value > 0 for _, value, _ in items)
    if has_neg and has_pos:
        zero, scale = (left + right) / 2, (right - left) / 2 / span
    elif has_neg:
        zero, scale = right, (right - left) / span
    else:
        zero, scale = left, (right - left) / span
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="{height}" viewBox="0 0 960 {height}" role="img">',
              f'<title>{E(title)}</title><rect width="960" height="{height}" fill="white"/>',
              '<g font-family="Segoe UI,Microsoft YaHei,sans-serif" fill="#253b37">',
              f'<text x="{left}" y="36" font-size="13">{E(unit)}</text>',
              f'<path d="M{zero:.2f} 52V{height - 24}" stroke="#c5d2cc"/>']
    for index, (name, value, _) in enumerate(items):
        y = 64 + index * row_h
        color = COLORS[index % len(COLORS)]
        end = zero + value * scale
        bar_x, bar_w = (end, zero - end) if value < 0 else (zero, end - zero)
        pieces.append(f'<rect x="{bar_x:.2f}" y="{y}" width="{max(bar_w, 0):.2f}" height="18" fill="{color}"/>')
        pieces.append(f'<text x="16" y="{y + 14}" font-size="13">{E(str(name))}</text>')
        pieces.append(f'<text x="872" y="{y + 14}" font-size="12">{value:,.2f}</text>')
    return ("".join(pieces) + "</g></svg>").encode()


def contribution_chart(spec, metrics, labels):
    group = spec.get("group")
    if not group:
        raise ValueError("contribution chart requires group")
    rows = [row for row in metrics.get("derived", []) if row.get("contribution_group") == group
            and row.get("operation") in {"contribution_delta", "contribution_residual"} and row.get("value") is not None]
    if not rows:
        return None
    if any(not _sensor_tower(row) for row in rows):
        raise ValueError("contribution chart requires Sensor Tower values")
    ordered = []
    for selector in spec.get("selectors") or []:
        matched = [row for row in rows if row.get("operation") == "contribution_delta"
                   and all(row.get(key) == value for key, value in selector.items())]
        if len(matched) != 1:
            return None
        ordered.append(matched[0])
    if not spec.get("selectors"):
        ordered = [row for row in rows if row["operation"] == "contribution_delta"]
    residual = [row for row in rows if row["operation"] == "contribution_residual"]
    if len(residual) != 1:
        raise ValueError("contribution chart requires one unlisted remainder")
    ordered.extend(residual)
    items = [(row.get("label") or (labels or {}).get(row.get("entity_id"), row.get("entity_id")), row["value"], "") for row in ordered]
    caption = "未列入产品的合计 = 品类净变化 − 已列入产品增减之和。分母接近零时不计算份额。"
    return signed_bars(spec["title"], items, label(ordered[0].get("unit", ""))), ordered, caption, []


def explicit_chart(spec, metrics, labels=None):
    """Return SVG and metadata from only the supplied records and specification."""
    chart_id = ident(spec["id"])
    kind = spec.get("kind", "market_monthly")
    extra_blobs = []
    if "source_group" in spec:
        raise ValueError("chart source selection is retired")
    if kind == "market_trend" and len(spec.get("selectors", [])) > 6:
        parts = split_market_trend(spec, metrics, labels)
        if not parts:
            result = None
        else:
            extra_blobs = [part[0] for part in parts[1:]]
            rows = [row for part in parts for row in part[1]]
            events = [event for part in parts for event in part[4]]
            result = parts[0][0], rows, " ".join(part[2] for part in parts) + parts[0][3], events
    elif kind in {"market_trend", "market_dual_axis"}:
        result = market_chart(spec, metrics, labels)
    elif kind == "market_rank":
        result = rank_chart(spec, metrics, labels)
    elif kind == "market_contribution":
        result = contribution_chart(spec, metrics, labels)
    elif kind in {"review_weekly", "review_versions", "review_themes"}:
        result = review_chart(spec, metrics)
    elif kind in {"market_monthly", "market_yoy"}:
        selector = spec["selector"]
        if set(selector) != {"entity_type", "entity_id", "country", "platform", "metric"}:
            raise ValueError("chart requires a complete selector")
        series = "yoy" if kind == "market_yoy" else "monthly"
        pool = metrics["cells"] if series == "monthly" else metrics["yoy"]["monthly"]
        cells = sorted([c for c in pool if all(c.get(k) == v for k, v in selector.items())], key=lambda c: c["month"])
        if len({c["month"] for c in cells}) != len(cells):
            raise ValueError("chart contains duplicate series months")
        usable = [c for c in cells if c["value"] is not None]
        if cells:
            by_month = {c["month"]: c for c in cells}
            cells = [by_month.get(month, {"month": month, "value": None, "unit": usable[0]["unit"] if usable else ""})
                     for month in months(cells[0]["month"]+"-01", cells[-1]["month"]+"-01")]
        result = None
        if usable:
            if any(c.get("status") != "available" for c in usable):
                raise ValueError("chart requires usable observations")
            if (len({c.get("scope_signature") for c in usable}) != 1
                    or len({c.get("selected_source_group", tuple(c.get("source_groups", []))) for c in usable}) != 1):
                raise ValueError("chart observations have incompatible scopes")
            first = usable[0]
            caption = ("Sensor Tower 估算 · " + members_text(first.get("geography"), "countries")
                       + " · " + members_text(first.get("store_scope"), "stores") + " · "
                       + cells[0]["month"] + " 至 " + cells[-1]["month"] + " · "
                       + ("同口径上年同期比较" if series == "yoy" else first["unit"]) + "。缺失留白。")
            result = chart_svg(cells, spec["title"], series), usable, caption, []
    else:
        raise ValueError("unsupported chart kind")
    record = dict(id=chart_id, title=spec["title"], kind=kind, status="missing")
    if not result:
        return dict(record, reason="没有可绘制的同口径数值或评论样本"), None
    blob, rows, caption, events = result
    for row in rows:
        if row.get("status") == "conflict":
            raise ValueError("chart requires usable observations")
        if row["value"] is not None:
            decimal_value(row["value"])
    refs = list(dict.fromkeys([e for r in rows for e in r["evidence_ids"]]
                              + [e for event in events for e in event["evidence_ids"]]))
    limits = list(dict.fromkeys(note for r in rows for note in r.get("scope_limitations", [])))
    packed = dict(record, status="available", mime="image/svg+xml", sha256=digest(blob),
                  evidence_ids=refs, data_evidence_ids=list(dict.fromkeys(e for r in rows for e in r["evidence_ids"])),
                  events=events, caption="；".join([caption, *limits]),
                  product_id=rows[0]["entity_id"] if kind.startswith("review_") else None)
    if extra_blobs:
        packed["_extra_blobs"] = extra_blobs
    return packed, blob

"""Report presentation only; never change research observations or coverage."""
from __future__ import annotations
import html
import math
from pathlib import Path

STYLE = (Path(__file__).resolve().parents[1] / "assets/report.css").read_text(encoding="utf-8-sig")
E = lambda value: html.escape(str(value), quote=True)
LABELS = {"downloads": "下载量", "revenue": "收入", "product": "产品", "category": "品类", "unknown": "未说明",
          "US": "美国", "GB": "英国", "DE": "德国", "FR": "法国", "CA": "加拿大", "AU": "澳大利亚",
          "IT": "意大利", "ES": "西班牙",
          "app_store": "App Store", "google_play": "Google Play",
          "android_unspecified": "Android（商店未明确）", "ios": "App Store", "android": "Android（商店未明确）",
          "iap_net": "内购净收入（按来源定义）", "iap_gross": "内购流水（按来源定义）",
          "provider_reported": "来源未细分定义"}

def label(value):
    return LABELS.get(value, str(value)) if value is not None else "未说明"


def members_text(scope, field):
    if not scope:
        return "未说明"
    values = scope.get(field, [])
    members = "、".join(label(v) for v in values)
    title = scope.get("label")
    if title:
        separators = str.maketrans(dict.fromkeys("+＋/／、,，", " "))
        if title.translate(separators).split() == members.translate(separators).split():
            return title
    return f"{title}（{members}）" if title and title != scope.get("id") else members or title or "未说明"


def short_number(value):
    for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= scale:
            return f"{value / scale:,.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def chart_svg(cells, title, series):
    """Only map existing values to SVG geometry; missing values remain missing."""
    usable = [c for c in cells if c["value"] is not None]
    maximum = max(0, max(c["value"] for c in usable))
    minimum = min(0, min(c["value"] for c in usable))
    if maximum == minimum:
        maximum = 1
    raw_step = (maximum - minimum) / 5
    power = 10 ** math.floor(math.log10(raw_step))
    tick_step = next(n * power for n in (1, 2, 2.5, 5, 10) if n * power >= raw_step)
    maximum = math.ceil(maximum / tick_step) * tick_step
    minimum = math.floor(minimum / tick_step) * tick_step
    span = maximum - minimum
    top, bottom, left, right = 87, 300, 72, 916
    def y(value):
        return top + (bottom - top) * (maximum - value) / span
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="390" viewBox="0 0 960 390" role="img">',
             f'<title>{E(title)}（估算）</title><desc>仅展示取得的数值，缺失不表示零。详细口径见报告。</desc>',
             '<rect width="960" height="390" fill="#ffffff"/>',
             '<g font-family="Segoe UI,Microsoft YaHei,sans-serif">',
             f'<text x="27" y="31" font-size="18" font-weight="600" fill="#172b32">{E(title[:34] + ("…" if len(title) > 34 else ""))}</text>',
             f'<text x="27" y="56" font-size="12" fill="#71877b">估算 · {"同比变化（%）" if series == "yoy" else label(usable[0]["unit"])}</text>']
    for tick in range(round(span / tick_step) + 1):
        value = minimum + tick_step * tick
        yy = y(value)
        parts += [f'<line x1="{left}" x2="{right}" y1="{yy:.2f}" y2="{yy:.2f}" stroke="#e7eee8" stroke-dasharray="3 5"/>',
                  f'<text x="{left-13}" y="{yy+4:.2f}" text-anchor="end" font-size="12" fill="#87998e">{E(short_number(value))}</text>']
    zero = y(0)
    parts.append(f'<line x1="{left}" x2="{right}" y1="{zero:.2f}" y2="{zero:.2f}" stroke="#becfc3"/>')
    step = (right - left) / len(cells)
    width = min(64, step * .47)
    for n, cell in enumerate(cells):
        x = left + (n + .5) * step
        parts.append(f'<text x="{x:.2f}" y="333" text-anchor="middle" font-size="12" fill="#71877b">{E(cell["month"])}</text>')
        if cell["value"] is None:
            parts += [f'<rect x="{x-width/2:.2f}" y="{zero-28:.2f}" width="{width}" height="28" rx="5" fill="#fbf6eb" stroke="#e4d8bc" stroke-dasharray="3 3"/>',
                      f'<text x="{x:.2f}" y="{zero-10:.2f}" text-anchor="middle" font-size="12" fill="#986524">缺失</text>']
            continue
        value = cell["value"]
        value_y = y(value)
        color = "#b9675b" if value < 0 else "#167666"
        label_y = value_y + 18 if value < 0 else value_y - 10
        full = f'{cell["month"]}：{value:,.2f} {cell["unit"]}（估算）'
        parts += [f'<g><title>{E(full)}</title><rect x="{x-width/2:.2f}" y="{min(zero,value_y):.2f}" width="{width}" height="{max(.8,abs(zero-value_y)):.2f}" rx="4" fill="{color}"/>',
                  f'<text x="{x:.2f}" y="{label_y:.2f}" text-anchor="middle" font-size="12" font-weight="600" fill="#294d3c">{E(short_number(value))}{"%" if series == "yoy" else ""}</text></g>']
    parts.append('<text x="27" y="373" font-size="12" fill="#87998e">缺失单独标注 · 未插值或外推 · 详细来源与统计范围见图注及数值表</text></g></svg>')
    return "".join(parts).encode("utf-8")

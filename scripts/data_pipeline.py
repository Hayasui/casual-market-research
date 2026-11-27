"""Stage 1 pipeline: run init, OSS truncation fetch, ST/metadata normalization and tagging bundle assembly.

Pure Python standard library. No third-party packages required.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
import io, os, tempfile, time, zipfile
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import METRIC_SOURCE, digest, ident, months, read, write
from scopes import decimal_value, month_period, number
from source_metrics import series_key
from tool_results import payload_state

IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)
CRASH_NOISE_RE = re.compile(
    r"\b(crash(?:es|ed|ing)?|won'?t\s+open|won'?t\s+load|freeze(?:s|d)?|black\s+screen|login\s+error|bug\s+fix)\b",
    re.I,
)
CN_ONLY_MARKERS = ("仅中国大陆", "中国大陆", "境内发行", "大陆发行", "CN_ONLY")


def load_configs():
    cfg_dir = SKILL_ROOT / "configs"
    return (
        read(cfg_dir / "workspace.json"),
        read(cfg_dir / "subgenres.json"),
        read(cfg_dir / "tag_blacklist.json"),
    )


def _norm_path_str(path_value) -> str:
    return str(path_value).replace("\\", "/").rstrip("/").lower()


def validate_out_dir(out_dir: Path, workspace_cfg: dict) -> Path:
    resolved = out_dir.resolve()
    raw_norm = _norm_path_str(out_dir)
    res_norm = _norm_path_str(resolved)
    skill_norm = _norm_path_str(SKILL_ROOT)
    if workspace_cfg.get("paths", {}).get("forbid_writing_inside_skill_dir", True):
        if res_norm == skill_norm or res_norm.startswith(skill_norm + "/"):
            raise ValueError(f"output directory cannot be inside skill directory: {resolved}")
    for blocked in workspace_cfg.get("paths", {}).get("readonly_paths_blacklist", []):
        b_norm = _norm_path_str(blocked)
        for cand in (raw_norm, res_norm):
            if cand == b_norm or cand.startswith(b_norm + "/"):
                raise ValueError(f"output directory hits readonly blacklist: {blocked}")
    return resolved


def shift_ym(ym: str, delta_months: int) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", ym):
        raise ValueError(f"window-end must be YYYY-MM, got: {ym}")
    year, month = int(ym[:4]), int(ym[5:7])
    if not 1 <= month <= 12:
        raise ValueError(f"invalid month in window-end: {ym}")
    total = year * 12 + (month - 1) + delta_months
    new_y, new_m = divmod(total, 12)
    return f"{new_y:04d}-{new_m + 1:02d}"


def ym_end_date(ym: str) -> str:
    year, month = int(ym[:4]), int(ym[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def build_window_spec(window_end: str, total_months: int = 6, recent_months: int = 3, lookback_months: int = 12) -> dict:
    start_ym = shift_ym(window_end, -(total_months - 1))
    prior_end_ym = shift_ym(window_end, -recent_months)
    recent_start_ym = shift_ym(window_end, -(recent_months - 1))
    month_list = months(f"{start_ym}-01", ym_end_date(window_end))
    new_rel_start = shift_ym(window_end, -(lookback_months - 1))
    new_rel_months = [m.replace("-", "/") for m in months(f"{new_rel_start}-01", ym_end_date(window_end))]
    return {
        "window": {"start": f"{start_ym}-01", "end": ym_end_date(window_end)},
        "months": month_list,
        "prior_3m": {"start": f"{start_ym}-01", "end": ym_end_date(prior_end_ym)},
        "recent_3m": {"start": f"{recent_start_ym}-01", "end": ym_end_date(window_end)},
        "new_release_months": new_rel_months,
        "new_release_cutoff_date": f"{new_rel_start}-01",
    }


def build_rolling_new_release_window(run_date: date | str) -> dict:
    if isinstance(run_date, str):
        run_date = date.fromisoformat(run_date)
    previous_year = run_date.year - 1
    clamped = run_date.month == 2 and run_date.day == 29
    start = run_date.replace(year=previous_year, day=28 if clamped else run_date.day)
    return {
        "start": start.isoformat(),
        "end": run_date.isoformat(),
        "inclusive": True,
        "rule": "rolling_1y_from_run_date",
        "leap_day_clamped": clamped,
    }


def strip_url_query(url: str) -> str:
    if not isinstance(url, str) or not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def unwrap_mcp_payload(obj):
    """Unpack MCP wrappers (upstream_response or content[].text JSON) into a plain dict/list."""
    current = obj
    for _ in range(4):
        if isinstance(current, str):
            try:
                current = json.loads(current)
                continue
            except ValueError:
                break
        if isinstance(current, dict):
            if "upstream_response" in current and current["upstream_response"] is not None:
                current = current["upstream_response"]
                continue
            if "result" in current and isinstance(current["result"], (dict, str)):
                current = current["result"]
                continue
            if isinstance(current.get("content"), list):
                text_blocks = [b.get("text") for b in current["content"] if isinstance(b, dict) and b.get("type") == "text"]
                if text_blocks:
                    try:
                        current = json.loads(text_blocks[0])
                        continue
                    except ValueError:
                        break
        break
    return current


def extract_jsonpath_rows(payload, rows_path: str):
    if not rows_path:
        return None
    parts = [p for p in rows_path.lstrip("$").split(".") if p]
    cur = payload
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur if isinstance(cur, list) else None


def detect_image_format(raw: bytes):
    for sig, ext, mime in IMAGE_MAGIC:
        if raw.startswith(sig):
            return ext, mime
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None, None


def sanitize_id_slug(value: str, prefix: str = "id") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    slug = f"{prefix}_{cleaned}" if prefix else cleaned
    return slug[:78] or f"{prefix}_item"


def normalize_name(name: str) -> str:
    return re.sub(r"[\W_]+", "", str(name or "").lower())


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


def parse_date_flexible(raw_val, *, require_day: bool = False) -> str | None:
    if not raw_val or not isinstance(raw_val, str):
        return None
    text = raw_val.strip()
    if text in {"暂无", "null", "None", "-", ""}:
        return None
    m = re.search(r"(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    if require_day:
        return None
    m2 = re.search(r"(\d{4})[.\-/](\d{1,2})", text)
    if m2:
        y, mo = int(m2.group(1)), int(m2.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-01"
    return None


def safe_rel_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        parts = path.as_posix().split("research/")
        if len(parts) > 1:
            return "research/" + parts[-1]
        return path.name


def cmd_init_run(args) -> int:
    workspace_cfg, subgenres_cfg, _ = load_configs()
    if args.preset not in subgenres_cfg["presets"]:
        raise ValueError(f"unknown preset: {args.preset}")
    out_dir = validate_out_dir(Path(args.out_dir), workspace_cfg)
    gdef = subgenres_cfg["global_st_defaults"]
    run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()
    release_window = build_rolling_new_release_window(run_date)
    cutoff_date = release_window["start"]
    release_months = [m.replace("-", "/") for m in months(cutoff_date, release_window["end"])]
    win = build_window_spec(
        args.window_end or shift_ym(run_date.isoformat()[:7], -1),
        total_months=int(gdef.get("window_complete_months", 6)),
        recent_months=int(gdef.get("recent_growth_months", 3)),
    )
    preset_cfg = subgenres_cfg["presets"][args.preset]
    for rel in (
        "research/raw",
        "research/data",
        "research/charts",
        "research/media/icons",
        "research/media/screenshots",
        "research/screenshots",
    ):
        (out_dir / rel).mkdir(parents=True, exist_ok=True)
    default_title = (
        f"合成品类题材与呈现 Tag 市场研究看板（{win['months'][0]} 至 {win['months'][-1]}）"
        if args.preset == "pilot"
        else f"休闲游戏四品类题材与呈现 Tag 市场研究看板（{win['months'][0]} 至 {win['months'][-1]}）"
    )
    state = {
        "schema_version": "1.0.0",
        "preset": args.preset,
        "report_title": args.title or default_title,
        "out_dir": str(out_dir),
        "window": win["window"],
        "months": win["months"],
        "prior_3m": win["prior_3m"],
        "recent_3m": win["recent_3m"],
        "run_date": run_date.isoformat(),
        "new_release_window": release_window,
        "new_release_cutoff_date": cutoff_date,
        "new_release_months": release_months,
        "recommended_daily_query_start_date": shift_ym(cutoff_date[:7], -2) + "-01",
        "enabled_subgenres": list(preset_cfg["enabled_subgenres"]),
        "market_overview_reference_subgenres": list(preset_cfg["market_overview_reference_subgenres"]),
        "html_script_mode": preset_cfg["html_script_mode"],
        "current_stage": "initialized",
        "vocab_signed_off": False,
        "blind_qc_passed": False,
        "visual_qa_passed": False,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    state_path = out_dir / "research/run_state.json"
    write(state_path, state)
    print(json.dumps({"status": "initialized", "run_state": str(state_path), "preset": args.preset,
                      "window": win["window"], "new_release_months": release_months}, ensure_ascii=False))
    return 0


def cmd_fetch_oss(args) -> int:
    raw_path = Path(args.raw_file).resolve()
    out_path = Path(args.out_file).resolve()
    raw_obj = read(raw_path)
    unwrapped = unwrap_mcp_payload(raw_obj)
    state = payload_state(raw_obj)
    if state["status"] != "incomplete":
        write(out_path, unwrapped)
        print(json.dumps({"status": "not_truncated", "output": str(out_path)}, ensure_ascii=False))
        return 0
    url = state.get("full_url")
    if not url or not isinstance(url, str):
        raise ValueError("truncated payload has no data_url")
    req = Request(url, headers={"User-Agent": "casual-tag-market-research/1.0"})
    with urlopen(req, timeout=args.timeout) as resp:
        body = resp.read().decode("utf-8-sig")
    fetched = json.loads(body)
    rows_path = unwrapped.get("rows_path") if isinstance(unwrapped, dict) else None
    expected_rows = state.get("expected_rows")
    actual_rows = None
    if isinstance(fetched, list):
        actual_rows = len(fetched)
        if isinstance(unwrapped, dict) and rows_path:
            merged = json.loads(json.dumps(unwrapped))
            parts = [p for p in rows_path.lstrip("$").split(".") if p]
            target = merged
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = fetched
            merged["truncated"] = False
            merged["oss_fetched"] = True
            merged["oss_url_sanitized"] = strip_url_query(url)
            merged.pop("data_url", None)
            fetched = merged
    elif isinstance(fetched, dict):
        rows = extract_jsonpath_rows(fetched, rows_path)
        if rows is not None:
            actual_rows = len(rows)
        fetched["truncated"] = False
        fetched["oss_fetched"] = True
        fetched["oss_url_sanitized"] = strip_url_query(url)
        fetched.pop("data_url", None)
    if expected_rows is not None and actual_rows is not None and actual_rows < int(expected_rows):
        raise ValueError(f"OSS download row mismatch: expected {expected_rows}, got {actual_rows}")
    write(out_path, fetched)
    print(json.dumps({"status": "fetched", "output": str(out_path), "expected_rows": expected_rows,
                      "actual_rows": actual_rows}, ensure_ascii=False))
    return 0


def _load_checked_raw(base_dir: Path, rel_or_abs: str):
    path = Path(rel_or_abs)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    raw = read(path)
    st = payload_state(raw)
    if st["status"] == "incomplete":
        raise ValueError(f"raw file is truncated; run fetch-oss first: {safe_rel_path(path, base_dir)}")
    return path, unwrap_mcp_payload(raw)


def _extract_market_monthly(raw_obj, metric: str, target_field: str | None = None,
                            allowed_values: set[str] | None = None,
                            excluded_values: set[str] | None = None) -> dict[str, Decimal]:
    rows = raw_obj.get("data", []) if isinstance(raw_obj, dict) else raw_obj
    if not isinstance(rows, list):
        raise ValueError("market size raw response must contain list data")
    value_keys = ("revenue_usd", "revenue", "revenueInUsdAbsolute") if metric == "revenue" else ("downloads", "downloadsAbsolute")
    monthly: dict[str, Decimal] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if target_field and target_field in row:
            val_label = str(row[target_field])
            if allowed_values is not None and val_label not in allowed_values:
                continue
            if excluded_values is not None and val_label in excluded_values:
                continue
        raw_date = str(row.get("date") or row.get("month") or "")
        if len(raw_date) < 7:
            continue
        ym = raw_date[:7]
        val = None
        for k in value_keys:
            if row.get(k) is not None:
                val = decimal_value(row[k])
                break
        if val is None:
            continue
        monthly[ym] = monthly.get(ym, Decimal("0")) + val
    return monthly


def _validate_yilan_app_info(raw_obj) -> tuple[bool, dict]:
    """Detect silent fake-zero (code==1 with APP_ID==null) per yilan-rules.md."""
    if not isinstance(raw_obj, dict):
        return False, {}
    data = raw_obj.get("data", raw_obj)
    if not isinstance(data, dict):
        return False, {}
    app_id = data.get("APP_ID")
    if app_id is None or str(app_id).strip() in {"", "null", "None", "暂无"}:
        return False, data
    return True, data


def _filter_reviews(raw_obj, max_reviews: int = 30) -> list[dict]:
    reviews = raw_obj.get("reviews", []) if isinstance(raw_obj, dict) else (raw_obj if isinstance(raw_obj, list) else [])
    high, low, mid = [], [], []
    seen_ids = set()
    for r in reviews:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "")
        if rid and rid in seen_ids:
            continue
        content = " ".join(str(r.get("content") or "").split())
        title = " ".join(str(r.get("title") or "").split())
        combined = f"{title} {content}".strip()
        if len(combined.split()) < 10 and len(combined) < 30:
            continue
        if CRASH_NOISE_RE.search(combined) and len(combined.split()) < 22:
            continue
        if rid:
            seen_ids.add(rid)
        entry = {
            "id": rid or f"r_{len(seen_ids) + 1}",
            "date": str(r.get("date") or "")[:10],
            "rating": r.get("rating"),
            "country": r.get("country") or "",
            "title": title[:140],
            "content": content[:500],
        }
        rating = r.get("rating")
        if isinstance(rating, (int, float)) and rating >= 4:
            high.append(entry)
        elif isinstance(rating, (int, float)) and rating <= 2:
            low.append(entry)
        else:
            mid.append(entry)
    picked = high[: max_reviews // 2] + low[: max_reviews // 3] + mid[: max_reviews // 6]
    leftover = high[max_reviews // 2 :] + low[max_reviews // 3 :] + mid[max_reviews // 6 :]
    while len(picked) < max_reviews and leftover:
        picked.append(leftover.pop(0))
    return picked[:max_reviews]


def _download_image(url: str, target_stem: Path, timeout: int = 20) -> tuple[Path | None, str | None, str | None]:
    if not url or not isinstance(url, str):
        return None, None, "empty_url"
    try:
        req = Request(url, headers={"User-Agent": "casual-tag-market-research/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        ext, mime = detect_image_format(raw)
        if not ext or not mime:
            return None, None, "invalid_image_signature"
        out_path = target_stem.with_suffix(ext)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
        return out_path, mime, None
    except Exception as exc:
        return None, None, str(exc)


def _find_existing_local_image(target_stem: Path) -> tuple[Path | None, str | None]:
    for ext, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                      (".webp", "image/webp"), (".gif", "image/gif")):
        cand = target_stem.with_suffix(ext)
        if cand.is_file():
            raw = cand.read_bytes()
            d_ext, d_mime = detect_image_format(raw)
            if d_ext and d_mime:
                return cand, d_mime
    return None, None


def _make_st_record(rec_id: str, entity_type: str, entity_id: str, metric: str, month: str,
                    value: Decimal | None, evidence_ids: list[str], geo_label: str,
                    scope_limitations: list[str] | None = None) -> dict:
    rec = {
        "id": ident(rec_id),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "country": "global",
        "platform": "unified",
        "metric": metric,
        "month": month,
        "period": month_period(month),
        "value": None if value is None else number(value),
        "unit": "USD" if metric == "revenue" else "downloads",
        "source_group": METRIC_SOURCE,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "geography": {"id": "global", "label": geo_label, "countries": [geo_label]},
        "store_scope": {"id": "unified", "label": "App Store + Google Play", "stores": ["app_store", "google_play"]},
        "scope_limitations": list(dict.fromkeys(scope_limitations or [])),
    }
    if metric == "revenue":
        rec["revenue_basis"] = "iap_net"
    else:
        rec["download_basis"] = "provider_reported"
    if value is not None:
        rec["value_decimal"] = str(value)
    return rec


def _make_clean_source(provider: str, params: dict, scope: str, retrieved_at: str,
                       raw_rel: str | None = None, url: str | None = None, text: str = "") -> dict:
    item = {
        "provider": provider,
        "params": params,
        "observation_scope": scope,
        "retrieved_at": retrieved_at,
        "status": "ok",
    }
    if raw_rel:
        item["raw_file"] = raw_rel
    if url:
        item["url"] = strip_url_query(url)
    if text:
        item["text"] = text[:800]
    return item


def _st_metric_files(manifest_section, metric):
    files = manifest_section.get(metric) or []
    return [files] if isinstance(files, str) else files


def _st_metric_value(row, metric):
    keys = ("revenue", "revenue_usd", "revenueInUsdAbsolute") if metric == "revenue" else ("downloads", "downloadsAbsolute")
    return next((decimal_value(row[key]) for key in keys if row.get(key) is not None), None)


def _read_revenue_ranking(path):
    _, payload = _load_checked_raw(Path.cwd(), path)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{path} 缺少榜单数组")
    ranking = []
    for index, row in enumerate(rows, 1):
        uid = str(row.get("unified_app_id") or "").strip() if isinstance(row, dict) else ""
        if not uid:
            raise ValueError(f"{path} 第 {index} 行缺少 unified_app_id")
        revenue = _st_metric_value(row, "revenue")
        if revenue is None:
            raise ValueError(f"{path} 第 {index} 行缺少收入")
        ranking.append((uid, revenue))
    return ranking


def cmd_check_coverage(args) -> int:
    result = {"merged_count": 0, "attributable_count": 0, "unattributable_rows": 0,
              "boundary_passed": False, "errors": []}
    errors = result["errors"]
    try:
        tolerance = decimal_value(args.tolerance)
        if not 0 <= tolerance <= 1:
            raise ValueError("tolerance 必须介于 0 和 1 之间")
        merged = _read_revenue_ranking(args.merged)
        result["merged_count"] = len(merged)
        singles = [row for path in args.singles for row in _read_revenue_ranking(path)]
        single_ids = {uid for uid, _ in singles}
        merged_ids = {uid for uid, _ in merged}
        unattributable = sum(uid not in single_ids for uid, _ in merged)
        outside_max = max((revenue for uid, revenue in singles if uid not in merged_ids), default=None)
        boundary_passed = outside_max is None or bool(merged and merged[-1][1] >= outside_max * (1 - tolerance))
        result.update(attributable_count=len(merged) - unattributable,
                      unattributable_rows=unattributable, boundary_passed=boundary_passed)
        if unattributable:
            errors.append(f"合并榜有 {unattributable} 行无法归入单子类并集")
        if not boundary_passed:
            errors.append("合并榜末位收入低于榜外最高收入的容差下限")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))

    result["status"] = "failed" if errors else "passed"
    if errors:
        result["reason"] = "。".join(errors)
        result["next_action"] = "核对合并榜和各单子类榜的查询口径、统一应用 ID 与收入，再重跑覆盖检查。"
    if args.output:
        write(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if errors else 0


def _load_product_series(manifest, out_dir, window, win_months, retrieved_at, st_sources, *,
                         cutoff_date, new_release_end):
    monthly = {metric: {} for metric in ("revenue", "downloads")}
    source_ids = {metric: {} for metric in monthly}
    first_positive = {}
    daily_min_date = None
    daily_query_start = manifest.get("st_daily_query_start_date")
    for metric in monthly:
        for idx, fpath in enumerate(_st_metric_files(manifest.get("st_performance_metrics") or {}, metric), 1):
            path, raw = _load_checked_raw(out_dir, fpath)
            sid = ident(f"st_perf_{metric[:3]}_{idx}")
            st_sources[sid] = _make_clean_source(
                METRIC_SOURCE,
                {"metric": metric, "os": "unified", "regions": [], "start_date": window["start"],
                 "end_date": window["end"], "date_granularity": "month"},
                "候选与入围产品近 6 个月全球月度估算",
                retrieved_at, raw_rel=safe_rel_path(path, out_dir),
            )
            for row in raw.get("data", []) if isinstance(raw, dict) else raw:
                uid = str(row.get("unified_app_id") or "")
                ym = str(row.get("date") or row.get("month") or "")[:7]
                if not uid or ym not in win_months:
                    continue
                ids = source_ids[metric].setdefault(uid, [])
                if sid not in ids:
                    ids.append(sid)
                val = _st_metric_value(row, metric)
                if val is not None:
                    monthly[metric].setdefault(uid, {})[ym] = val

        for fpath in _st_metric_files(manifest.get("st_daily_performance_metrics") or {}, metric):
            _, raw = _load_checked_raw(out_dir, fpath)
            for row in raw.get("data", []) if isinstance(raw, dict) else raw:
                uid = str(row.get("unified_app_id") or "")
                day = parse_date_flexible(row.get("date"), require_day=True)
                if day and (daily_min_date is None or day < daily_min_date):
                    daily_min_date = day
                val = _st_metric_value(row, metric)
                if uid and day and day <= new_release_end and val is not None and val > 0:
                    first_positive[uid] = min(day, first_positive.get(uid, day))
    if daily_min_date is not None and daily_min_date >= cutoff_date:
        raise ValueError(
            f"st_daily_performance_metrics coverage starts at {daily_min_date}, must be earlier than cutoff_date {cutoff_date}"
        )
    return monthly, source_ids, first_positive, daily_min_date, daily_query_start


def _load_union_audit(path, out_dir, sg_id, window, monthly, source_ids, retrieved_at, st_sources, *, cutoff_date):
    abs_path, audit = _load_checked_raw(out_dir, path)
    expected = {"subgenre_id": sg_id, "window_start": window["start"], "window_end": window["end"]}
    for key, value in expected.items():
        if key in audit and audit[key] != value:
            raise ValueError(f"union_audit {key}={audit[key]!r}; expected {value!r}")
    daily_query_start = audit.get("daily_query_start_date")
    if daily_query_start and daily_query_start >= cutoff_date:
        raise ValueError(
            f"union_audit daily_query_start_date {daily_query_start} must be earlier than cutoff_date {cutoff_date}"
        )
    candidates = audit["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("union_audit candidates must be an array")
    sid = ident(f"st_new_union_{sg_id}")
    st_sources[sid] = _make_clean_source(
        METRIC_SOURCE, {"subgenre_id": sg_id, "start_date": window["start"], "end_date": window["end"]},
        "离线候选并集审计所载 ST 月度估算", retrieved_at, raw_rel=safe_rel_path(abs_path, out_dir),
    )
    for row in candidates:
        uid = str(row["unified_app_id"])
        for metric, field in (("revenue", "monthly_revenue_usd"), ("downloads", "monthly_downloads")):
            if field not in row:
                continue
            fill = set(row.get("pre_launch_zero_months") or [])
            monthly[metric][uid] = {
                ym: decimal_value(value) for ym, value in (row[field] or {}).items()
                if value is not None and not (ym in fill and decimal_value(value) == 0)
            }
            source_ids[metric][uid] = [sid]
    return candidates


def _audit_new_candidate(uid, row, launch_date, monthly, prior_months, recent_months, preexisting_reason):
    win_months = prior_months + recent_months
    pre_launch = {m for m in win_months if launch_date and m < launch_date[:7]}
    result = {"unified_app_id": uid, "app_name": row.get("unified_app_name") or row.get("app_name") or uid,
              "effective_launch_date": launch_date, "selected": False, "is_boundary_seat": False}
    zeros, missing = set(), set()
    for metric, month_key, prior_key, recent_key, delta_key in (
        ("revenue", "monthly_revenue_usd", "prior_3m_rev_usd", "recent_3m_rev_usd", "rev_delta_3m_usd"),
        ("downloads", "monthly_downloads", "prior_3m_dl", "recent_3m_dl", "dl_delta_3m"),
    ):
        raw = monthly[metric].get(uid, {})
        series = {m: raw.get(m) for m in win_months}
        for month, value in series.items():
            if month in pre_launch and value is not None and value > 0:
                raise ValueError(f"{uid}: pre-launch month {month} has positive {metric}={value}")
            if value is None and month in pre_launch:
                series[month] = Decimal("0.00")
                zeros.add(month)
            elif value is None:
                missing.add(month)
        result[month_key] = {m: number(v) if v is not None else None for m, v in series.items()}
        prior = [series[m] for m in prior_months]
        recent = [series[m] for m in recent_months]
        p_sum = sum(prior, Decimal("0")) if None not in prior else None
        r_sum = sum(recent, Decimal("0")) if None not in recent else None
        result[prior_key] = number(p_sum) if p_sum is not None else None
        result[recent_key] = number(r_sum) if r_sum is not None else None
        result[delta_key] = number(r_sum - p_sum) if p_sum is not None and r_sum is not None else None
    result["pre_launch_zero_months"] = sorted(zeros)
    result["missing_post_launch_months"] = sorted(missing)
    result["exclusion_reason"] = preexisting_reason or ("上市当月或之后存在缺月" if missing else None)
    return result


def _rank_new_candidates(candidates, rule, fixed):
    def revenue_key(c):
        return -c["rev_delta_3m_usd"], -c["dl_delta_3m"], c["unified_app_id"]

    def downloads_key(c):
        return -c["dl_delta_3m"], -c["rev_delta_3m_usd"], c["unified_app_id"]

    complete = [c for c in candidates if c["ranking_eligible"]]
    revenue = sorted((c for c in complete if c["rev_delta_3m_usd"] > 0), key=revenue_key)
    if rule == "rev_delta_5":
        for candidate in complete:
            if candidate["rev_delta_3m_usd"] <= 0:
                candidate["exclusion_reason"] = "近段净收入增量不大于零"
                candidate["ranking_eligible"] = False
        return revenue[:fixed], revenue[fixed] if len(revenue) > fixed else None
    if rule == "rev_delta_3_dl_delta_2":
        revenue_seats = revenue[:3]
        downloads = sorted((c for c in complete if c not in revenue_seats), key=downloads_key)
        count = fixed - 3
        return revenue_seats + downloads[:count], downloads[count] if len(downloads) > count else None
    raise ValueError(f"unknown new_apps_target rule: {rule}")


def cmd_build(args) -> int:
    workspace_cfg, subgenres_cfg, _ = load_configs()
    out_dir = validate_out_dir(Path(args.out_dir), workspace_cfg)
    run_state = read(out_dir / "research/run_state.json")
    preset = args.preset or run_state["preset"]
    if preset not in subgenres_cfg["presets"]:
        raise ValueError(f"unknown preset: {preset}")
    preset_cfg = subgenres_cfg["presets"][preset]
    manifest = read(Path(args.manifest).resolve())
    retrieved_at = manifest.get("retrieved_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    geo_label = subgenres_cfg["global_st_defaults"]["geo_scope_public_label"]
    window = run_state["window"]
    win_months = run_state["months"]
    prior_months = months(run_state["prior_3m"]["start"], run_state["prior_3m"]["end"])
    recent_months = months(run_state["recent_3m"]["start"], run_state["recent_3m"]["end"])
    cutoff_date = run_state["new_release_cutoff_date"]
    new_release_end = run_state["new_release_window"]["end"] if "new_release_window" in run_state else window["end"]
    prov_map = workspace_cfg["sanitization_policy"]["provider_display_map"]

    st_sources: dict[str, dict] = {}
    meta_sources: dict[str, dict] = {}
    records: list[dict] = []
    entity_monthly_avail: dict[tuple[str, str, str], set[str]] = {}

    # 1. Macro game_product_model market size
    macro_cfg = subgenres_cfg["macro_benchmarks"]["product_model_series"]
    macro_files = manifest.get("macro_market_size", {})
    for metric in ("revenue", "downloads"):
        fpath = macro_files.get(metric)
        if not fpath:
            continue
        abs_p, raw_obj = _load_checked_raw(out_dir, fpath)
        src_id = ident(f"st_macro_pm_{metric[:3]}")
        st_sources[src_id] = _make_clean_source(
            METRIC_SOURCE,
            {"breakdown": "game_product_model", "metric": metric, "os": "unified", "regions": [],
             "start_date": window["start"], "end_date": window["end"], "date_granularity": "month"},
            f"全球移动游戏产品模型月度{('IAP净收入' if metric == 'revenue' else '下载量')}估算",
            retrieved_at,
            raw_rel=safe_rel_path(abs_p, out_dir),
        )
        for pm in macro_cfg["track_values"]:
            monthly = _extract_market_monthly(raw_obj, metric, target_field="game_product_model", allowed_values={pm})
            eid = sanitize_id_slug(pm.lower(), "macro")
            for ym in win_months:
                val = monthly.get(ym)
                records.append(_make_st_record(f"rec_{eid}_{metric[:3]}_{ym.replace('-', '')}",
                                               "category", eid, metric, ym, val, [src_id], geo_label))
                if val is not None:
                    entity_monthly_avail.setdefault(("category", eid, metric), set()).add(ym)

    # 2. Subgenre market size and combined Merge audit
    sub_market_files = manifest.get("subgenre_market_size", {})
    sub_monthly_cache: dict[tuple[str, str], dict[str, Decimal]] = {}
    sub_source_ids: dict[tuple[str, str], str] = {}
    subgenre_breakdown_audit: dict[str, dict] = {}
    ref_subgenres = list(preset_cfg["market_overview_reference_subgenres"])
    for sg_id in ref_subgenres:
        sg_cfg = subgenres_cfg["subgenres"][sg_id]
        sg_files = sub_market_files.get(sg_id, {})
        allowed = set(sg_cfg["st_filter"].get("market_size_target_values") or []) or None
        excluded = {e["value"] for e in sg_cfg.get("excluded_sub_genres", [])} or None
        scope_limitations = None
        if sg_id == "merge":
            scope_limitations = ["由 Sensor Tower Match Merge 2 与 Match Merge 3 同口径月度估算值合并得出，非 ST 原生单一分类"]
            merge_components = ("Match Merge 2", "Match Merge 3")
            merge_audit = {label: {} for label in merge_components}
            subgenre_breakdown_audit[sg_id] = merge_audit
        for metric in ("revenue", "downloads"):
            fpath = sg_files.get(metric)
            if not fpath:
                raise ValueError(f"missing required ST market size file for {sg_id}.{metric}")
            abs_p, raw_obj = _load_checked_raw(out_dir, fpath)
            src_id = ident(f"st_mkt_{sg_id}_{metric[:3]}")
            sub_source_ids[(sg_id, metric)] = src_id
            st_sources[src_id] = _make_clean_source(
                METRIC_SOURCE,
                {"breakdown": sg_cfg["st_filter"]["market_size_breakdown"], "metric": metric,
                 "os": "unified", "regions": [], "start_date": window["start"], "end_date": window["end"],
                 "date_granularity": "month", "subgenre_id": sg_id},
                f"{sg_cfg['display_name']}品类全球月度{('IAP净收入' if metric == 'revenue' else '下载量')}估算",
                retrieved_at,
                raw_rel=safe_rel_path(abs_p, out_dir),
            )
            if sg_id == "merge":
                raw_rows = raw_obj.get("data", []) if isinstance(raw_obj, dict) else raw_obj
                if not isinstance(raw_rows, list):
                    raise ValueError("market size raw response must contain list data")
                component_monthly = {}
                for label in merge_components:
                    component_rows = [row for row in raw_rows if isinstance(row, dict) and row.get("game_sub_genre") == label]
                    if not component_rows:
                        raise ValueError(f"merge.{metric} market size missing required {label} rows")
                    series = _extract_market_monthly(component_rows, metric)
                    component_monthly[label] = series
                    monthly_key, total_key = (
                        ("monthly_revenue_usd", "revenue_6m_usd") if metric == "revenue"
                        else ("monthly_downloads", "downloads_6m")
                    )
                    merge_audit[label][monthly_key] = {ym: number(series[ym]) if ym in series else None for ym in win_months}
                    merge_audit[label][total_key] = (
                        number(sum(series[ym] for ym in win_months)) if all(ym in series for ym in win_months) else None
                    )
                monthly = {
                    ym: sum(series[ym] for series in component_monthly.values())
                    for ym in win_months if all(ym in series for series in component_monthly.values())
                }
            else:
                monthly = _extract_market_monthly(raw_obj, metric, target_field="game_sub_genre",
                                                  allowed_values=allowed, excluded_values=excluded)
            sub_monthly_cache[(sg_id, metric)] = monthly
            for ym in win_months:
                val = monthly.get(ym)
                records.append(_make_st_record(f"rec_{sg_id}_{metric[:3]}_{ym.replace('-', '')}",
                                               "category", sg_id, metric, ym, val, [src_id], geo_label,
                                               scope_limitations=scope_limitations))
                if val is not None:
                    entity_monthly_avail.setdefault(("category", sg_id, metric), set()).add(ym)

    if preset_cfg.get("include_merge_combined_row", False) and subgenres_cfg.get("macro_benchmarks", {}).get("merge_combined_reference"):
        mc_cfg = subgenres_cfg["macro_benchmarks"]["merge_combined_reference"]
        comps = mc_cfg["component_subgenres"]
        if all((c, "revenue") in sub_monthly_cache and (c, "downloads") in sub_monthly_cache for c in comps):
            for metric in ("revenue", "downloads"):
                ev_ids = [sub_source_ids[(c, metric)] for c in comps]
                for ym in win_months:
                    vals = [sub_monthly_cache[(c, metric)].get(ym) for c in comps]
                    combined_val = sum(vals, Decimal("0")) if all(v is not None for v in vals) else None
                    records.append(_make_st_record(
                        f"rec_merge_combined_{metric[:3]}_{ym.replace('-', '')}",
                        "category", "merge_combined", metric, ym, combined_val, ev_ids, geo_label,
                        scope_limitations=[mc_cfg["public_note"]],
                    ))
                    if combined_val is not None:
                        entity_monthly_avail.setdefault(("category", "merge_combined", metric), set()).add(ym)

    # 3. Parse sub-app details overrides & yilan category rank index (for m_app_id / icon_url)
    sub_app_tag_overrides: dict[str, dict] = {}
    for ios_id, fpath in (manifest.get("st_sub_app_details") or {}).items():
        _, raw_obj = _load_checked_raw(out_dir, fpath)
        intel = raw_obj.get("game_intel_data", raw_obj) if isinstance(raw_obj, dict) else {}
        sub_app_tag_overrides[str(ios_id)] = {
            "genre": (intel.get("genre") or {}).get("name") if isinstance(intel.get("genre"), dict) else intel.get("genre"),
            "sub_genre": (intel.get("sub_genre") or {}).get("name") if isinstance(intel.get("sub_genre"), dict) else intel.get("sub_genre"),
            "product_model": (intel.get("product_model") or {}).get("name") if isinstance(intel.get("product_model"), dict) else intel.get("product_model"),
        }

    yl_rank_index_by_name: dict[str, dict] = {}
    yl_rank_index_by_key: dict[str, dict] = {}
    for sg_id, fpath in (manifest.get("yilan_category_rankings") or {}).items():
        _, raw_obj = _load_checked_raw(out_dir, fpath)
        data = raw_obj.get("data", raw_obj) if isinstance(raw_obj, dict) else {}
        rows = []
        if isinstance(data, dict):
            for key in ("income_ranking", "download_ranking", "list", "rows"):
                if isinstance(data.get(key), list):
                    rows.extend(data[key])
        elif isinstance(data, list):
            rows = data
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Strictly record requested sg_id, NEVER trust returned parent category_name.
            info = {
                "m_app_id": str(row.get("m_app_id") or row.get("app_id") or "").strip() or None,
                "icon_url": row.get("gameIcon") or row.get("icon_url") or row.get("icon") or None,
                "app_key": row.get("app_key") or None,
                "requested_subgenre": sg_id,
            }
            nm = normalize_name(row.get("name") or row.get("app_name") or row.get("game") or "")
            if nm and nm not in yl_rank_index_by_name:
                yl_rank_index_by_name[nm] = info
            if info["app_key"]:
                yl_rank_index_by_key[info["app_key"]] = info

    # 4. Build product roster ordered by cross_category_priority
    enabled_sgs = sorted(
        preset_cfg["enabled_subgenres"],
        key=lambda sid: int(subgenres_cfg["subgenres"][sid]["cross_category_priority"]),
    )
    roster_by_uid: dict[str, dict] = {}
    claimed_primary_subgenre: dict[str, str] = {}
    excluded_audit: list[dict] = []
    cross_column_dedup_log: list[dict] = []
    top_app_files = manifest.get("top_apps", {})
    new_app_files = manifest.get("new_apps", {})
    whitelist_resolved = manifest.get("whitelist_resolved", {})
    monthly, product_source_ids, first_positive, daily_min_date, daily_query_start = _load_product_series(
        manifest, out_dir, window, win_months, retrieved_at, st_sources, cutoff_date=cutoff_date,
        new_release_end=new_release_end,
    )
    union_files = manifest.get("union_audit") or {}
    union_path = args.union_audit or (union_files if isinstance(union_files, str) else None)
    if union_path:
        resolved_path, union = _load_checked_raw(out_dir, union_path)
        union_files = {union.get("subgenre_id", "merge"): str(resolved_path)}
    union_candidates = {
        sg_id: _load_union_audit(path, out_dir, sg_id, window, monthly, product_source_ids, retrieved_at, st_sources,
                                 cutoff_date=cutoff_date)
        for sg_id, path in union_files.items() if sg_id in enabled_sgs
    }
    yl_info_map = manifest.get("yilan_app_info") or {}
    new_release_audit = {}
    union_launch_dates = {}
    late_release_reason = "上线晚于 6 个月完整月统计窗末日，暂缺完整月序列"

    def _row_excluded_reason(sg_cfg: dict, row: dict) -> str | None:
        nm_norm = normalize_name(row.get("unified_app_name") or row.get("app_name") or "")
        for exc in sg_cfg.get("excluded_apps", []):
            if normalize_name(exc["app_name"]) == nm_norm:
                return exc["reason"]
        excl_sub = {e["value"] for e in sg_cfg.get("excluded_sub_genres", [])}
        for ios_id in row.get("ios_app_ids") or []:
            ov = sub_app_tag_overrides.get(str(ios_id))
            if ov and ov.get("sub_genre") in excl_sub:
                return f"ST美区iOS子类为 {ov['sub_genre']}，按配置排除"
        return None

    def _register_product(uid: str, row: dict, sg_id: str, role: str, *,
                          subgenre_rank: int | None = None, download_rank: int | None = None,
                          include_in_auto_cr10: bool = False, public_badge: str = "",
                          release_stage: str = "global") -> dict:
        existing = roster_by_uid.get(uid)
        if existing is None:
            ios_ids = [str(x) for x in (row.get("ios_app_ids") or []) if str(x)]
            and_ids = [str(x) for x in (row.get("android_app_ids") or []) if str(x)]
            cand_keys = [f"appstore_{i}" for i in ios_ids] + [f"googleplay_{p}" for p in and_ids]
            existing = {
                "unified_app_id": uid,
                "entity_id": sanitize_id_slug(uid, "app"),
                "app_name": row.get("unified_app_name") or row.get("app_name") or uid,
                "publisher_name": row.get("unified_publisher_name") or row.get("publisher_name") or "未提供",
                "primary_subgenre": sg_id,
                "subgenre_memberships": [],
                "roles": [],
                "subgenre_rank": subgenre_rank,
                "download_rank": download_rank,
                "include_in_auto_cr10": include_in_auto_cr10,
                "is_new_release": role == "new_release",
                "is_boundary_seat": False,
                "release_stage": release_stage,
                "public_badge": public_badge,
                "ios_app_ids": ios_ids,
                "android_app_ids": and_ids,
                "candidate_app_keys": cand_keys,
                "matched_app_key": None,
                "m_app_id": None,
                "publisher_start": None,
                "cn_ios_only": False,
                "geo_note": "",
                "st_game_iq": {},
                "icon_file": None,
                "icon_mime": None,
                "screenshot_files": [],
                "metadata_status": "pending",
                "yilan_fake_zero_detected": False,
            }
            roster_by_uid[uid] = existing
        if sg_id not in existing["subgenre_memberships"]:
            existing["subgenre_memberships"].append(sg_id)
        if role not in existing["roles"]:
            existing["roles"].append(role)
        if role == "new_release":
            existing["is_new_release"] = True
            existing["release_stage"] = release_stage
        if subgenre_rank is not None and existing["subgenre_rank"] is None:
            existing["subgenre_rank"] = subgenre_rank
        if download_rank is not None and existing["download_rank"] is None:
            existing["download_rank"] = download_rank
        if include_in_auto_cr10:
            existing["include_in_auto_cr10"] = True
        if public_badge and not existing["public_badge"]:
            existing["public_badge"] = public_badge
        return existing

    for sg_id in enabled_sgs:
        sg_cfg = subgenres_cfg["subgenres"][sg_id]
        sg_top = top_app_files.get(sg_id, {})
        rev_path = sg_top.get("revenue")
        if not rev_path:
            raise ValueError(f"missing ST top revenue file for {sg_id}")
        abs_rev, rev_obj = _load_checked_raw(out_dir, rev_path)
        rev_src_id = ident(f"st_top_{sg_id}_rev")
        st_sources[rev_src_id] = _make_clean_source(
            METRIC_SOURCE,
            {"metric": "revenue", "os": "unified", "regions": [], "start_date": window["start"],
             "end_date": window["end"], "subgenre_id": sg_id, "include_app_details": True},
            f"{sg_cfg['display_name']}近6月全球收入Top榜单",
            retrieved_at,
            raw_rel=safe_rel_path(abs_rev, out_dir),
        )
        rev_rows = rev_obj.get("data", []) if isinstance(rev_obj, dict) else rev_obj
        top_n = int(sg_cfg.get("top_revenue_n", 10))
        subgenre_top10_uids: set[str] = set()
        rank_counter = 0
        for row in rev_rows:
            if rank_counter >= top_n:
                break
            uid = str(row.get("unified_app_id") or "")
            if not uid:
                continue
            exc_reason = _row_excluded_reason(sg_cfg, row)
            if exc_reason:
                excluded_audit.append({"subgenre_id": sg_id, "app_name": row.get("unified_app_name"),
                                       "unified_app_id": uid, "stage": "top_revenue", "reason": exc_reason})
                continue
            if uid in claimed_primary_subgenre and claimed_primary_subgenre[uid] != sg_id:
                cross_column_dedup_log.append({
                    "unified_app_id": uid, "app_name": row.get("unified_app_name"),
                    "kept_in": claimed_primary_subgenre[uid], "skipped_in": sg_id, "pool": "top_revenue",
                })
                continue
            rank_counter += 1
            claimed_primary_subgenre[uid] = sg_id
            subgenre_top10_uids.add(uid)
            _register_product(uid, row, sg_id, "top10_revenue", subgenre_rank=rank_counter, include_in_auto_cr10=True)

        # Download supplement (simulation & hybrid_puzzle in full mode)
        dl_cfg = sg_cfg.get("download_supplement", {})
        dl_enabled = bool(dl_cfg.get("enabled")) and sg_id in preset_cfg.get("download_supplement_enabled_subgenres", [])
        dl_path = sg_top.get("downloads")
        if dl_enabled and dl_path:
            abs_dl, dl_obj = _load_checked_raw(out_dir, dl_path)
            dl_src_id = ident(f"st_top_{sg_id}_dl")
            st_sources[dl_src_id] = _make_clean_source(
                METRIC_SOURCE,
                {"metric": "downloads", "os": "unified", "regions": [], "start_date": window["start"],
                 "end_date": window["end"], "subgenre_id": sg_id, "include_app_details": True},
                f"{sg_cfg['display_name']}近6月全球下载增补榜",
                retrieved_at,
                raw_rel=safe_rel_path(abs_dl, out_dir),
            )
            dl_rows = dl_obj.get("data", []) if isinstance(dl_obj, dict) else dl_obj
            supp_count = 0
            supp_cap = int(dl_cfg.get("top_n", 5))
            for idx, row in enumerate(dl_rows, 1):
                if supp_count >= supp_cap:
                    break
                uid = str(row.get("unified_app_id") or "")
                if not uid or uid in subgenre_top10_uids:
                    continue
                exc_reason = _row_excluded_reason(sg_cfg, row)
                if exc_reason:
                    excluded_audit.append({"subgenre_id": sg_id, "app_name": row.get("unified_app_name"),
                                           "unified_app_id": uid, "stage": "download_supplement", "reason": exc_reason})
                    continue
                if uid in claimed_primary_subgenre and claimed_primary_subgenre[uid] != sg_id:
                    cross_column_dedup_log.append({
                        "unified_app_id": uid, "app_name": row.get("unified_app_name"),
                        "kept_in": claimed_primary_subgenre[uid], "skipped_in": sg_id, "pool": "download_supplement",
                    })
                    continue
                supp_count += 1
                claimed_primary_subgenre[uid] = sg_id
                _register_product(uid, row, sg_id, "download_supplement", download_rank=idx,
                                  include_in_auto_cr10=False, public_badge=dl_cfg.get("public_badge", ""))

        # Whitelist exceptions (e.g. Match Factory! in hybrid_puzzle; isolated from CR10 denominator)
        for exc_item in sg_cfg.get("whitelist_exceptions", []):
            res_entry = whitelist_resolved.get(exc_item["app_name"]) or whitelist_resolved.get(str(exc_item.get("ios_app_id")))
            if not res_entry:
                continue
            uid = str(res_entry.get("unified_app_id") or "")
            if not uid or uid in subgenre_top10_uids:
                continue
            claimed_primary_subgenre.setdefault(uid, sg_id)
            _register_product(
                uid, res_entry, sg_id, "whitelist_exception",
                include_in_auto_cr10=bool(exc_item.get("include_in_auto_cr10", False)),
                public_badge=exc_item.get("public_badge", ""),
            )

        # Freeze the candidate union after loading six months and checking launch dates.
        sg_new = new_app_files.get(sg_id, {})
        seen_new_uids: dict[str, tuple[dict, str]] = {}
        recent_revenue_ranks: dict[str, int] = {}
        if sg_id in union_candidates:
            for row in union_candidates[sg_id]:
                uid = str(row["unified_app_id"])
                stage = row.get("release_stage", "soft_launch" if row.get("soft_launch_status") == "verified" else "global")
                channels = {source["channel"]: source.get("rank") for source in row.get("source_channels") or []}
                if not row.get("discovery_channel"):
                    channel = next((key for key in ("sensor_tower_revenue_top20", "sensor_tower_downloads_top20")
                                    if key in channels), None)
                    if channel is None and "third_party_new_apps" in channels:
                        channel = "third_party"
                    row = {**row, "discovery_channel": channel}
                rank = channels.get("sensor_tower_revenue_top20")
                if isinstance(rank, int) and rank > 0:
                    recent_revenue_ranks[uid] = rank
                seen_new_uids[uid] = (row, stage)
        else:
            for metric in ("revenue", "downloads"):
                fpath = sg_new.get(metric)
                if not fpath:
                    continue
                path, raw = _load_checked_raw(out_dir, fpath)
                sid = ident(f"st_new_{sg_id}_{metric[:3]}")
                st_sources[sid] = _make_clean_source(
                    METRIC_SOURCE,
                    {"metric": metric, "os": "unified", "regions": [], "start_date": run_state["recent_3m"]["start"],
                     "end_date": run_state["recent_3m"]["end"], "subgenre_id": sg_id,
                     "release_date_ww": run_state["new_release_months"], "primary_category": "6014",
                     "custom_fields_include_unified_apps": False},
                    f"{sg_cfg['display_name']}近12月全球首发新品初筛榜",
                    retrieved_at, raw_rel=safe_rel_path(path, out_dir),
                )
                rows = raw.get("data", []) if isinstance(raw, dict) else raw
                for rank, row in enumerate(rows[:20], 1):
                    uid = str(row.get("unified_app_id") or "")
                    if uid:
                        if metric == "revenue":
                            recent_revenue_ranks.setdefault(uid, rank)
                        row = {"discovery_channel": f"sensor_tower_{metric}_top20", **row}
                        prior_row = seen_new_uids.get(uid, ({}, "global"))[0]
                        seen_new_uids[uid] = ({**row, **{k: v for k, v in prior_row.items() if v}}, "global")
            for row in sg_new.get("soft_launch_resolved") or []:
                uid = str(row.get("unified_app_id") or "")
                if uid:
                    row = {"discovery_channel": "third_party", **row}
                    prior_row, stage = seen_new_uids.get(uid, ({}, "soft_launch"))
                    seen_new_uids[uid] = ({**row, **{k: v for k, v in prior_row.items() if v}}, stage)

        candidates = []
        for uid, (row, _) in seen_new_uids.items():
            reason = _row_excluded_reason(sg_cfg, row)
            if claimed_primary_subgenre.get(uid, sg_id) != sg_id:
                reason = "已归入优先级更高的品类"
            if row.get("eligibility") == "excluded_republish_or_port":
                reason = row.get("exclusion_reason") or "换皮重发或移植"
            launch_date = first_positive.get(uid)
            if not launch_date and sg_id in union_candidates:
                launch_date = parse_date_flexible(row.get("first_observed_date_st"), require_day=True)
                if launch_date and launch_date <= new_release_end:
                    union_launch_dates.setdefault(uid, launch_date)
            if not launch_date:
                reason = "excluded_launch_unverified"
                print(f"{sg_id} {uid}: {reason}（ST 日序列与并集审计均无可用首发日）", file=sys.stderr)
            elif launch_date < cutoff_date:
                reason = f"最早发行日 {launch_date} 早于近12月门槛 {cutoff_date}"
            elif launch_date > new_release_end:
                reason = f"最早发行日 {launch_date} 晚于新品滚动窗口末日 {new_release_end}"
            elif launch_date > window["end"] and not reason:
                reason = late_release_reason

            channel = row.get("discovery_channel")
            fetched_revenue = bool(monthly["revenue"].get(uid))
            has_st_revenue = fetched_revenue or uid in recent_revenue_ranks
            third_party_no_revenue = channel == "third_party" and not has_st_revenue
            if third_party_no_revenue and not reason:
                reason = "第三方发现的候选暂无 ST 收入"
            candidate = _audit_new_candidate(uid, row, launch_date, monthly, prior_months, recent_months, reason)
            candidate["discovery_channel"] = channel
            candidate["revenue_status"] = "ok" if has_st_revenue else "no_data"
            candidate["fetch_status"] = "fetched" if fetched_revenue else (
                "not_needed" if third_party_no_revenue else "pending"
            )
            candidate["ranking_eligible"] = not candidate["exclusion_reason"]
            candidate["is_cutoff_boundary_date"] = launch_date == cutoff_date
            candidates.append(candidate)

        if not recent_revenue_ranks:
            comparable = [c for c in candidates if c["discovery_channel"] != "third_party"
                          and c["revenue_status"] == "ok" and c["recent_3m_rev_usd"] is not None
                          and c["exclusion_reason"] != late_release_reason]
            comparable.sort(key=lambda c: (-c["recent_3m_rev_usd"], c["unified_app_id"]))
            recent_revenue_ranks = {c["unified_app_id"]: rank for rank, c in enumerate(comparable, 1)}
        for candidate in candidates:
            candidate["daily_status"] = (
                "required" if candidate["discovery_channel"] != "third_party"
                and recent_revenue_ranks.get(candidate["unified_app_id"], 13) <= 12 else "not_needed"
            )

        target = sg_cfg["new_apps_target"]
        fixed = int(target.get("fixed", target.get("max", 5)))
        selected, sixth = _rank_new_candidates(candidates, target["rule"], fixed)
        fifth = selected[4] if len(selected) >= 5 else None
        gap = relative_gap = None
        if fifth and sixth:
            fifth_delta = decimal_value(fifth["rev_delta_3m_usd"])
            gap = fifth_delta - decimal_value(sixth["rev_delta_3m_usd"])
            if fifth_delta > 0:
                relative_gap = gap / fifth_delta
                fifth["is_boundary_seat"] = 0 <= relative_gap < Decimal("0.15")
        sixth_delta = sixth["rev_delta_3m_usd"] if sixth else None
        new_release_audit[sg_id] = {
            "rule": target["rule"], "target_count": fixed, "selected_count": len(selected),
            "sixth_seat": sixth, "sixth_seat_rev_delta_usd": sixth_delta,
            "fifth_to_sixth_rev_delta_gap_usd": number(gap) if gap is not None else None,
            "fifth_to_sixth_relative_gap": number(relative_gap) if relative_gap is not None else None,
            "candidates": candidates,
        }
        for rank, candidate in enumerate(selected, 1):
            uid = candidate["unified_app_id"]
            row, stage = seen_new_uids[uid]
            candidate["selected"] = True
            candidate["seat"] = rank
            claimed_primary_subgenre.setdefault(uid, sg_id)
            prod = _register_product(uid, row, sg_id, "new_release", release_stage=stage)
            prod.update(
                new_release_rank=rank, effective_launch_date=candidate["effective_launch_date"],
                is_boundary_seat=candidate["is_boundary_seat"],
                sixth_seat_rev_delta_usd=sixth_delta if rank == 5 else None,
                pre_launch_zero_months=candidate["pre_launch_zero_months"],
                missing_post_launch_months=candidate["missing_post_launch_months"],
            )
            for values_by_uid in monthly.values():
                values = values_by_uid[uid]
                for ym in win_months:
                    values.setdefault(ym, Decimal("0.00"))

        for candidate in candidates:
            if candidate["exclusion_reason"] and candidate["exclusion_reason"] != late_release_reason:
                excluded_audit.append({
                    "subgenre_id": sg_id, "app_name": candidate["app_name"],
                    "unified_app_id": candidate["unified_app_id"], "stage": "new_release",
                    "reason": candidate["exclusion_reason"],
                })

    # Emit product metrics only for the frozen roster.
    for metric, values_by_uid in monthly.items():
        for uid, prod in roster_by_uid.items():
            eid = prod["entity_id"]
            ev_ids = product_source_ids[metric].get(uid, [])
            if not ev_ids:
                continue
            for ym in win_months:
                val = values_by_uid.get(uid, {}).get(ym)
                records.append(_make_st_record(f"rec_{eid}_{metric[:3]}_{ym.replace('-', '')}",
                                               "product", eid, metric, ym, val, ev_ids, geo_label))
                if val is not None:
                    entity_monthly_avail.setdefault(("product", eid, metric), set()).add(ym)

    def complete_revenue(series_by_entity, entity_ids):
        series = [series_by_entity.get(eid, {}).get(ym) for eid in entity_ids for ym in win_months]
        return number(sum(series, Decimal("0"))) if series and None not in series else None

    frozen_roster_revenue = complete_revenue(monthly["revenue"], roster_by_uid)
    market_cr10_denominator = complete_revenue(
        {sg_id: sub_monthly_cache.get((sg_id, "revenue"), {}) for sg_id in ref_subgenres}, enabled_sgs[:1],
    )

    # 6. Per-product metadata enrichment, fake-zero checks, materials, reviews, icons & screenshots
    yl_desc_map = manifest.get("yilan_app_desc") or {}
    st_rev_map = manifest.get("st_reviews") or {}
    yl_mat_files = manifest.get("yilan_materials") or {}
    materials_by_m_app_id: dict[str, list[dict]] = {}
    mat_source_by_sg: dict[str, str] = {}
    for sg_id, fpath in yl_mat_files.items():
        abs_m, raw_m = _load_checked_raw(out_dir, fpath)
        msrc = ident(f"meta_ad_{sg_id}")
        mat_source_by_sg[sg_id] = msrc
        meta_sources[msrc] = _make_clean_source(
            prov_map.get("yilan_ad_creatives", "第三方投放监测素材"),
            {"subgenre_id": sg_id, "sort_field": "impression", "sort_order": "DESC"},
            "境外高曝光投放素材文案与封面（仅辅证）",
            retrieved_at,
            raw_rel=safe_rel_path(abs_m, out_dir),
        )
        rows = (raw_m.get("data", {}) if isinstance(raw_m, dict) else {}).get("rows", [])
        for r in rows if isinstance(rows, list) else []:
            slogan = " ".join(str(r.get("slogan") or "").split())
            cover = strip_url_query(r.get("material_cover") or "")
            app_infos = r.get("app_info") if isinstance(r.get("app_info"), list) else []
            for ai in app_infos:
                mid = str(ai.get("m_app_id") or "").strip()
                if not mid:
                    continue
                materials_by_m_app_id.setdefault(mid, []).append({
                    "slogan": slogan,
                    "impression": r.get("impression") or "",
                    "media": r.get("media") or "",
                    "cover_url": cover,
                    "source_id": msrc,
                    "marketing_only": True,
                })

    tagging_products: list[dict] = []
    fake_zero_count = 0
    matched_meta_count = 0
    launch_date_reconciliation_audit = []

    for uid, prod in roster_by_uid.items():
        eid = prod["entity_id"]
        prod["effective_launch_date"] = prod.get("effective_launch_date") or first_positive.get(uid) or union_launch_dates.get(uid)
        prod["is_cutoff_boundary_date"] = prod["effective_launch_date"] == cutoff_date
        # Attach sub_app_details Game IQ reference if available
        for ios_id in prod["ios_app_ids"]:
            if ios_id in sub_app_tag_overrides:
                prod["st_game_iq"] = sub_app_tag_overrides[ios_id]
                break

        # Link m_app_id & icon_url from rank index
        rank_hit = None
        for ck in prod["candidate_app_keys"]:
            if ck in yl_rank_index_by_key:
                rank_hit = yl_rank_index_by_key[ck]
                break
        if not rank_hit:
            rank_hit = yl_rank_index_by_name.get(normalize_name(prod["app_name"]))
        if rank_hit:
            prod["m_app_id"] = rank_hit.get("m_app_id")

        # Validate app_info (supports single path or list of fallback paths)
        info_entries = yl_info_map.get(uid) or []
        if isinstance(info_entries, str):
            info_entries = [info_entries]
        info_data = {}
        info_src_id = None
        for fpath in info_entries:
            abs_i, raw_i = _load_checked_raw(out_dir, fpath)
            ok, parsed = _validate_yilan_app_info(raw_i)
            if not ok:
                prod["yilan_fake_zero_detected"] = True
                fake_zero_count += 1
                continue
            info_data = parsed
            info_src_id = ident(f"meta_info_{eid[:24]}")
            meta_sources[info_src_id] = _make_clean_source(
                prov_map.get("yilan_store_metadata", "应用商店公开元数据"),
                {"app_id": str(parsed.get("APP_ID")), "market": parsed.get("market") or "store"},
                "应用商店基础信息与最早发行日",
                retrieved_at,
                raw_rel=safe_rel_path(abs_i, out_dir),
                url=parsed.get("downloadLink"),
            )
            prod["matched_app_key"] = f"{parsed.get('market', 'appstore')}_{parsed.get('APP_ID')}"
            prod["publisher_start"] = parse_date_flexible(parsed.get("publisher_start"))
            pub_area = str(parsed.get("publisher_area") or "")
            if any(m in pub_area for m in CN_ONLY_MARKERS):
                prod["cn_ios_only"] = True
                prod["geo_note"] = "仅在中国大陆 App Store 发行（计入 ST 全球 iOS 统计口径）"
            prod["metadata_status"] = "available"
            matched_meta_count += 1
            break

        publisher_start = prod["publisher_start"]
        effective_launch_date = prod["effective_launch_date"]
        reconciliation_reason = None
        if bool(publisher_start) != bool(effective_launch_date):
            reconciliation_reason = "publisher_start 缺失" if not publisher_start else "effective_launch_date 缺失"
        elif publisher_start and effective_launch_date and (
            (publisher_start < cutoff_date) != (effective_launch_date < cutoff_date)
        ):
            reconciliation_reason = "商店发行日与 ST 生效首发日分居新品门槛两侧"
        if reconciliation_reason:
            launch_date_reconciliation_audit.append({
                "unified_app_id": uid,
                "app_name": prod["app_name"],
                "publisher_start": publisher_start,
                "effective_launch_date": effective_launch_date,
                "cutoff_date": cutoff_date,
                "reason": reconciliation_reason,
            })

        # Parse app_detail_desc
        desc_text, subtitle = "", str(info_data.get("subtitle") or "")
        screenshot_urls: list[str] = []
        desc_src_id = None
        if uid in yl_desc_map:
            abs_d, raw_d = _load_checked_raw(out_dir, yl_desc_map[uid])
            ddata = raw_d.get("data", raw_d) if isinstance(raw_d, dict) else {}
            if isinstance(ddata, dict):
                desc_text = str(ddata.get("description") or "").strip()
                screenshot_urls = [u for u in (ddata.get("screenshot_urls") or []) if isinstance(u, str) and u.startswith("http")]
                desc_src_id = ident(f"meta_desc_{eid[:24]}")
                meta_sources[desc_src_id] = _make_clean_source(
                    prov_map.get("yilan_store_metadata", "应用商店公开元数据"),
                    {"app_key": prod["matched_app_key"] or (prod["candidate_app_keys"][0] if prod["candidate_app_keys"] else uid),
                     "fields": ["description", "screenshot_urls"]},
                    "英文商店完整描述与商店核心截图",
                    retrieved_at,
                    raw_rel=safe_rel_path(abs_d, out_dir),
                    text=desc_text[:600],
                )

        # Download or reuse local icon & up to 5 screenshots
        icon_stem = out_dir / "research/media/icons" / f"icon_{eid}"
        local_icon, local_mime = _find_existing_local_image(icon_stem)
        if not local_icon and not args.skip_media_download:
            icon_url = (rank_hit or {}).get("icon_url") or info_data.get("icon_url") or info_data.get("gameIcon")
            if icon_url:
                local_icon, local_mime, _ = _download_image(icon_url, icon_stem)
        if local_icon and local_mime:
            prod["icon_file"] = safe_rel_path(local_icon, out_dir)
            prod["icon_mime"] = local_mime
            prod["icon_source_id"] = info_src_id or desc_src_id

        max_shots = int(workspace_cfg["visual_review_policy"].get("store_screenshots_fetch_max", 5))
        shot_records = []
        for idx in range(1, max_shots + 1):
            shot_stem = out_dir / "research/media/screenshots" / f"{eid}_{idx:02d}"
            s_path, _ = _find_existing_local_image(shot_stem)
            if not s_path and not args.skip_media_download and idx <= len(screenshot_urls):
                s_path, _, _ = _download_image(screenshot_urls[idx - 1], shot_stem)
            if s_path:
                rel_s = safe_rel_path(s_path, out_dir)
                prod["screenshot_files"].append(rel_s)
                shot_src_id = ident(f"meta_shot_{eid[:20]}_{idx:02d}")
                meta_sources[shot_src_id] = _make_clean_source(
                    prov_map.get("yilan_store_metadata", "应用商店公开元数据"),
                    {"screenshot_index": idx, "app_name": prod["app_name"]},
                    f"商店核心截图第 {idx} 张（仅作视觉呈现核验，不内嵌成品HTML）",
                    retrieved_at,
                    raw_rel=rel_s,
                )
                shot_records.append({"index": idx, "file": rel_s, "source_id": shot_src_id})

        # Reviews
        reviews_clean = []
        rev_src_id = None
        if uid in st_rev_map:
            abs_r, raw_r = _load_checked_raw(out_dir, st_rev_map[uid])
            reviews_clean = _filter_reviews(raw_r, max_reviews=30)
            rev_src_id = ident(f"st_rev_{eid[:24]}")
            st_sources[rev_src_id] = _make_clean_source(
                METRIC_SOURCE,
                {"scope": "近60-90天英语/美区评论抽样", "sample_kept": len(reviews_clean)},
                "Sensor Tower 商店玩家评论原文样本",
                retrieved_at,
                raw_rel=safe_rel_path(abs_r, out_dir),
            )

        # Ad creatives (strictly marketing_only)
        ad_list = (materials_by_m_app_id.get(prod["m_app_id"] or "") or [])[:5]
        if prod["metadata_status"] == "pending":
            prod["metadata_status"] = "available" if desc_text else "missing"

        tagging_products.append({
            "unified_app_id": uid,
            "entity_id": eid,
            "app_name": prod["app_name"],
            "publisher_name": prod["publisher_name"],
            "subgenre_id": prod["primary_subgenre"],
            "roles": prod["roles"],
            "publisher_start": prod["publisher_start"],
            "cn_ios_only": prod["cn_ios_only"],
            "geo_note": prod["geo_note"],
            "layer1_store_facts": {
                "subtitle": subtitle,
                "description": desc_text,
                "description_source_id": desc_src_id,
                "info_source_id": info_src_id,
                "st_game_iq_reference_only": prod["st_game_iq"],
            },
            "layer2_visual_presentation": {
                "mandatory_review_range": workspace_cfg["visual_review_policy"]["mandatory_screenshots_for_tagging"],
                "screenshots": shot_records,
                "ad_cover_urls_on_demand": [a["cover_url"] for a in ad_list if a.get("cover_url")],
            },
            "layer3_voices_and_ads": {
                "reviews_source_id": rev_src_id,
                "reviews": reviews_clean,
                "ad_slogans_marketing_only": [
                    {"slogan": a["slogan"], "impression": a["impression"], "media": a["media"],
                     "source_id": a["source_id"], "marketing_only": True}
                    for a in ad_list if a.get("slogan")
                ],
            },
        })

    # 7. Build deterministic derived calculations matching market_calculations.py sorting
    keys_sorted = sorted({series_key(r) for r in records})
    key_to_total_idx = {k: idx for idx, k in enumerate(keys_sorted)}
    month_to_pos = {m: idx for idx, m in enumerate(win_months)}

    def cell_ptr(k, ym):
        return f"/cells/{key_to_total_idx[k] * len(win_months) + month_to_pos[ym]}"

    calculations: list[dict] = []
    derived_idx_by_id: dict[str, int] = {}
    next_derived = 0

    def add_calc(spec: dict, produced_count: int = 1):
        nonlocal next_derived
        calculations.append(spec)
        derived_idx_by_id[spec["id"]] = next_derived
        next_derived += produced_count

    all_entities = sorted({(k[0], k[1]) for k in keys_sorted})
    for etype, eid in all_entities:
        for metric in ("revenue", "downloads"):
            k = (etype, eid, "global", "unified", metric)
            avail = entity_monthly_avail.get((etype, eid, metric), set())
            if all(m in avail for m in prior_months):
                add_calc({
                    "id": ident(f"{eid}_{metric[:3]}_p3m"),
                    "operation": "sum",
                    "inputs": [{"json_pointer": cell_ptr(k, m), "match": {"entity_id": eid, "metric": metric, "month": m}}
                               for m in prior_months],
                })
            if all(m in avail for m in recent_months):
                add_calc({
                    "id": ident(f"{eid}_{metric[:3]}_r3m"),
                    "operation": "sum",
                    "inputs": [{"json_pointer": cell_ptr(k, m), "match": {"entity_id": eid, "metric": metric, "month": m}}
                               for m in recent_months],
                })
            p_id, r_id = f"{eid}_{metric[:3]}_p3m", f"{eid}_{metric[:3]}_r3m"
            if p_id in derived_idx_by_id and r_id in derived_idx_by_id:
                add_calc({
                    "id": ident(f"{eid}_{metric[:3]}_qoq3m"),
                    "operation": "percent_change",
                    "inputs": [
                        {"json_pointer": f"/derived/{derived_idx_by_id[r_id]}", "match": {"id": r_id}},
                        {"json_pointer": f"/derived/{derived_idx_by_id[p_id]}", "match": {"id": p_id}},
                    ],
                })
        # Approx RPD (6m revenue / 6m downloads)
        rev_k = (etype, eid, "global", "unified", "revenue")
        dl_k = (etype, eid, "global", "unified", "downloads")
        if (all(m in entity_monthly_avail.get((etype, eid, "revenue"), set()) for m in win_months)
                and all(m in entity_monthly_avail.get((etype, eid, "downloads"), set()) for m in win_months)):
            add_calc({
                "id": ident(f"{eid}_rpd6m"),
                "operation": "ratio",
                "inputs": [
                    {"json_pointer": f"/totals/{key_to_total_idx[rev_k]}", "match": {"entity_id": eid, "metric": "revenue"}},
                    {"json_pointer": f"/totals/{key_to_total_idx[dl_k]}", "match": {"entity_id": eid, "metric": "downloads"}},
                ],
            })

    # CR10 concentration (strictly auto-matched top10_revenue only!) & 3m contribution delta
    for sg_id in enabled_sgs:
        cat_rev_k = ("category", sg_id, "global", "unified", "revenue")
        if cat_rev_k not in key_to_total_idx:
            continue
        auto_cr10_eids = [
            p["entity_id"] for p in roster_by_uid.values()
            if p["primary_subgenre"] == sg_id and p["include_in_auto_cr10"]
            and all(m in entity_monthly_avail.get(("product", p["entity_id"], "revenue"), set()) for m in win_months)
        ]
        if auto_cr10_eids and all(m in entity_monthly_avail.get(("category", sg_id, "revenue"), set()) for m in win_months):
            cr_inputs = [
                {"json_pointer": f"/totals/{key_to_total_idx[('product', peid, 'global', 'unified', 'revenue')]}",
                 "match": {"entity_id": peid, "metric": "revenue"}}
                for peid in auto_cr10_eids
            ] + [{"json_pointer": f"/totals/{key_to_total_idx[cat_rev_k]}", "match": {"entity_id": sg_id, "metric": "revenue"}}]
            add_calc({"id": ident(f"{sg_id}_cr10_rev"), "operation": "concentration", "inputs": cr_inputs})

        # 3m revenue & downloads contribution (produces exact contribution_delta per paired product)
        for metric in ("revenue", "downloads"):
            m_cur_id, m_base_id = f"{sg_id}_{metric[:3]}_r3m", f"{sg_id}_{metric[:3]}_p3m"
            if m_cur_id not in derived_idx_by_id or m_base_id not in derived_idx_by_id:
                continue
            paired_eids = [
                p["entity_id"] for p in roster_by_uid.values()
                if p["primary_subgenre"] == sg_id
                and f"{p['entity_id']}_{metric[:3]}_r3m" in derived_idx_by_id
                and f"{p['entity_id']}_{metric[:3]}_p3m" in derived_idx_by_id
            ]
            if not paired_eids:
                continue
            contrib_inputs = [
                {"role": "market_current", "json_pointer": f"/derived/{derived_idx_by_id[m_cur_id]}", "match": {"id": m_cur_id}},
                {"role": "market_baseline", "json_pointer": f"/derived/{derived_idx_by_id[m_base_id]}", "match": {"id": m_base_id}},
            ]
            for peid in paired_eids:
                pc, pb = f"{peid}_{metric[:3]}_r3m", f"{peid}_{metric[:3]}_p3m"
                contrib_inputs.append({"role": "entity_current", "json_pointer": f"/derived/{derived_idx_by_id[pc]}", "match": {"id": pc}})
                contrib_inputs.append({"role": "entity_baseline", "json_pointer": f"/derived/{derived_idx_by_id[pb]}", "match": {"id": pb}})
            add_calc(
                {"id": ident(f"{sg_id}_contrib_{metric[:3]}3m"), "operation": "contribution", "inputs": contrib_inputs},
                produced_count=2 + 2 * len(paired_eids),
            )

    # 8. Write stage 1 outputs & chart specs (referencing ../data/market_result.json)
    market_input = {
        "window": window,
        "yoy": False,
        "sources": st_sources,
        "records": records,
        "calculations": calculations,
    }
    product_roster = {
        "schema_version": "1.0.0",
        "preset": preset,
        "window": window,
        "geo_scope": geo_label,
        "enabled_subgenres": enabled_sgs,
        "products": list(roster_by_uid.values()),
        "excluded_audit": excluded_audit,
        "cross_column_dedup_log": cross_column_dedup_log,
        "new_release_audit": new_release_audit,
        "launch_date_reconciliation_audit": launch_date_reconciliation_audit,
        "daily_min_date": daily_min_date,
        "daily_query_start": daily_query_start,
        "subgenre_breakdown_audit": subgenre_breakdown_audit,
        "frozen_roster_6m_revenue_usd": frozen_roster_revenue,
        "market_cr10_denominator_usd": market_cr10_denominator,
        "stats": {
            "total_products": len(roster_by_uid),
            "metadata_matched": matched_meta_count,
            "metadata_match_rate": round(matched_meta_count / max(1, len(roster_by_uid)), 4),
            "yilan_fake_zero_detected_count": fake_zero_count,
        },
    }
    for key in ("run_date", "new_release_window", "new_release_cutoff_date", "new_release_months",
                "recommended_daily_query_start_date"):
        if key in run_state:
            product_roster[key] = run_state[key]
    if manifest.get("ranking_coverage_audit"):
        _, coverage_audit = _load_checked_raw(out_dir, manifest["ranking_coverage_audit"])
        product_roster["ranking_coverage_audit"] = coverage_audit
    tagging_bundle = {
        "schema_version": "1.0.0",
        "preset": preset,
        "window": window,
        "products": tagging_products,
        "sources": {**st_sources, **meta_sources},
    }
    write(out_dir / "research/data/market_input.json", market_input)
    write(out_dir / "research/data/product_roster.json", product_roster)
    write(out_dir / "research/data/tagging_input_bundle.json", tagging_bundle)

    # Chart specs
    labels_map = {p["entity_id"]: clean_item_name(p["app_name"], 28) for p in roster_by_uid.values()}
    for sg_id, sg_cfg in subgenres_cfg["subgenres"].items():
        labels_map[sg_id] = sg_cfg["display_name"]
    labels_map["merge_combined"] = "合成合计（二合+三合）"
    labels_map["macro_casual"] = "Casual 大盘"

    chart_specs_written = []
    if all(m in entity_monthly_avail.get(("category", "macro_casual", "revenue"), set()) for m in win_months):
        spec = {
            "chart": {
                "id": "macro_dual_axis",
                "title": f"全球 Casual 休闲大盘月度收入与下载趋势（{win_months[0]}–{win_months[-1]}）",
                "kind": "market_dual_axis",
                "selectors": [
                    {"entity_type": "category", "entity_id": "macro_casual", "country": "global", "platform": "unified", "metric": "revenue"},
                    {"entity_type": "category", "entity_id": "macro_casual", "country": "global", "platform": "unified", "metric": "downloads"},
                ],
            },
            "data": "../data/market_result.json",
            "labels": labels_map,
        }
        p = out_dir / "research/charts/macro_dual_axis_spec.json"
        write(p, spec)
        chart_specs_written.append(safe_rel_path(p, out_dir))

    for sg_id in enabled_sgs:
        sg_name = subgenres_cfg["subgenres"][sg_id]["display_name"]
        if sg_id == "merge":
            dual_spec = {
                "chart": {
                    "id": "merge_dual_axis",
                    "title": f"合成品类月度净收入与下载趋势（{win_months[0]}–{win_months[-1]}）",
                    "kind": "market_dual_axis",
                    "selectors": [
                        {"entity_type": "category", "entity_id": "merge", "country": "global", "platform": "unified", "metric": "revenue"},
                        {"entity_type": "category", "entity_id": "merge", "country": "global", "platform": "unified", "metric": "downloads"},
                    ],
                },
                "data": "../data/market_result.json",
                "labels": labels_map,
            }
            dp = out_dir / "research/charts/merge_dual_axis_spec.json"
            write(dp, dual_spec)
            chart_specs_written.append(safe_rel_path(dp, out_dir))

        top_eids = [
            p["entity_id"] for p in roster_by_uid.values()
            if p["primary_subgenre"] == sg_id and p["include_in_auto_cr10"]
            and all(m in entity_monthly_avail.get(("product", p["entity_id"], "revenue"), set()) for m in win_months)
        ]
        if top_eids:
            rank_spec = {
                "chart": {
                    "id": f"subgenre_{sg_id}_rank",
                    "title": f"{sg_name} Top{len(top_eids)} 近 6 个月 ST 净收入排行",
                    "kind": "market_rank",
                    "period": {"kind": "interval", "start": window["start"], "end": window["end"]},
                    "selectors": [
                        {"entity_type": "product", "entity_id": peid, "country": "global", "platform": "unified", "metric": "revenue"}
                        for peid in top_eids
                    ],
                    "denominator": {"entity_type": "category", "entity_id": sg_id, "country": "global", "platform": "unified", "metric": "revenue"},
                },
                "data": "../data/market_result.json",
                "labels": labels_map,
            }
            rp = out_dir / f"research/charts/subgenre_{sg_id}_rank_spec.json"
            write(rp, rank_spec)
            chart_specs_written.append(safe_rel_path(rp, out_dir))

    run_state["current_stage"] = "stage1_done"
    run_state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write(out_dir / "research/run_state.json", run_state)

    print(json.dumps({
        "status": "built",
        "preset": preset,
        "products": len(roster_by_uid),
        "market_records": len(records),
        "derived_specs": len(calculations),
        "chart_specs": chart_specs_written,
        "excluded_audit_count": len(excluded_audit),
        "yilan_fake_zero_detected": fake_zero_count,
    }, ensure_ascii=False))
    return 0


AGENT_LEVEL_CHECKS_PENDING = (
    "mcp_st_readonly_probe", "mcp_metadata_readonly_probe", "remote_subagent_vision_probe_if_new_host",
)

_CHECKPOINT_SPECS = {
    "stage1_done": (("research/data/market_result.json", "research/data/product_roster.json", "research/data/tagging_input_bundle.json"), "stage2_draft_ready", ["references/stage2-tagging.md"], {"vocab_signed_off": False}, "python -B scripts/tag_qc.py validate-vocab --vocab {out_dir}/research/data/tag_vocab_draft.json --preset {preset} --draft-md {out_dir}/research/data/tag_vocab_draft.md"),
    "stage2_draft_ready": (("research/data/tag_vocab_draft.json", "research/data/tag_vocab_draft.md"), "stage2_verified", ["references/stage2-tagging.md"], {"vocab_signed_off": False}, "等待人工文字批复词表签字后落盘 tag_vocab.json，再完成 Pass 2 并跑 tag_qc.py validate-tags"),
    "stage2_verified": (("research/data/tag_vocab.json", "research/data/tags_final.json", "research/data/qc_report.json", "research/data/tagged_corpus.json"), "stage3_done", ["references/stage3-synthesis-films.md"], {"vocab_signed_off": True, "blind_qc_passed": True}, "按 references/stage3-synthesis-films.md 生成 supply_matrix.json、opportunities.json 与 film_refs.json"),
    "stage3_done": (("research/data/supply_matrix.json", "research/data/opportunities.json", "research/data/film_refs.json"), "stage4_built", ["references/stage4-html-delivery.md"], {}, "python -B scripts/compile_report.py build --out-dir {out_dir} --preset {preset}"),
    "stage4_built": (("research/delivery-input.json", "research/delivery.json"), "delivered", ["references/stage4-html-delivery.md"], {}, "完成无头浏览器目检并更新 research/delivery.json 后再运行 checkpoint --stage delivered"),
    "delivered": (("research/delivery-input.json", "research/delivery.json"), None, [], {}, "已完成交付收口（无需继续跃迁）"),
}


def _parse_gate_summary(raw_arg: str | None) -> dict:
    """Parse --gate-summary from inline JSON text or a JSON file path."""
    if raw_arg is None:
        return {}
    if not raw_arg.strip():
        raise ValueError("--gate-summary cannot be empty")
    try:
        parsed = json.loads(raw_arg)
    except ValueError:
        path = Path(raw_arg)
        if not path.is_file():
            raise ValueError(f"--gate-summary must be valid JSON or an existing file: {raw_arg}")
        parsed = read(path)
    if not isinstance(parsed, dict):
        raise ValueError("--gate-summary must resolve to a JSON object")
    return parsed


def _extract_open_l1_l2_gaps(out_dir: Path) -> dict[str, list[str]]:
    """Extract unclosed L1/L2 gap IDs using compile_report.py semantics."""
    path, result = out_dir / "research/data/gap_register.json", {"L1": [], "L2": []}
    if not path.is_file():
        return result
    raw = read(path)
    if isinstance(raw, dict) and "gaps" not in raw:
        return result
    gaps = raw.get("gaps") if isinstance(raw, dict) else raw
    if not isinstance(gaps, list):
        raise ValueError("gap_register.json must contain a list of gaps")
    for idx, gap in enumerate(gaps, 1):
        if not isinstance(gap, dict):
            raise ValueError(f"gap_register.json entry #{idx} must be an object")
        level, gid = gap.get("impact_level"), gap.get("gap_id")
        if level not in ("L1", "L2"):
            continue
        if not isinstance(gid, str) or not gid.strip():
            raise ValueError(f"gap_register.json entry #{idx} missing gap_id")
        st = str(gap.get("status") or "").strip().lower()
        sup = st == "superseded" and any(v.strip() if isinstance(v, str) else v for v in (gap.get(k) for k in ("superseded_by", "resolution", "reason")))
        if st not in {"closed", "resolved", "已关闭", "已解决"} and not sup:
            result[level].append(gid)
    return result


def _check_artifact(path: Path, rel: str):
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"missing or empty required artifact: {rel}")
    if path.suffix == ".json":
        obj = read(path)
        if not isinstance(obj, (dict, list)):
            raise ValueError(f"artifact must be a JSON object or array: {rel}")
        return obj
    if path.suffix == ".md":
        if not path.read_text(encoding="utf-8-sig").strip():
            raise ValueError(f"markdown artifact is blank: {rel}")
        return None
    with path.open("rb") as fh:
        if not fh.read(1):
            raise ValueError(f"artifact is unreadable: {rel}")
    return None


def _validate_stage_artifacts(out_dir: Path, stage: str) -> tuple[dict, dict | None]:
    """Validate required stage artifacts before writing any checkpoint output."""
    loaded_map = {rel: _check_artifact(out_dir / rel, rel) for rel in _CHECKPOINT_SPECS[stage][0]}
    delivery_obj = loaded_map.get("research/delivery.json", {})
    if "research/delivery.json" in loaded_map and not isinstance(delivery_obj, dict):
        raise ValueError("research/delivery.json must be a JSON object")
    corpus_obj = loaded_map.get("research/data/tagged_corpus.json")
    if "research/data/tagged_corpus.json" in loaded_map and not isinstance(corpus_obj, dict):
        raise ValueError("research/data/tagged_corpus.json must be a JSON object")
    if stage == "stage2_verified":
        qc = (corpus_obj or {}).get("qc_summary")
        if not corpus_obj or corpus_obj.get("status") != "verified" or not isinstance(qc, dict) or qc.get("passed") is not True:
            raise ValueError("tagged_corpus.json must have status='verified' and qc_summary.passed=true")
    if stage in {"stage4_built", "delivered"}:
        b64 = [p for p in out_dir.iterdir() if p.is_file() and p.name.endswith(".base64.html")]
        zips = [p for p in out_dir.iterdir() if p.is_file() and p.name.endswith("_bundle.zip")]
        if not b64 or not zips:
            raise ValueError("missing root deliverable (*.base64.html and *_bundle.zip required)")
        for path in (*b64, *zips):
            _check_artifact(path, path.name)
    if corpus_obj is None and (out_dir / "research/data/tagged_corpus.json").is_file():
        try:
            loaded = read(out_dir / "research/data/tagged_corpus.json")
            corpus_obj = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            corpus_obj = None
    return delivery_obj, corpus_obj


def _scan_manifest_paths(out_dir: Path) -> list[tuple[Path, str, str]]:
    """Collect research/ files (excluding manifest itself, *.tmp, __pycache__) and root deliverables."""
    items, research_dir = [], out_dir / "research"
    if research_dir.is_dir():
        for path in research_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(out_dir).as_posix()
            segs = rel.split("/")
            if rel == "research/data/packet_manifest.json" or "__pycache__" in segs or any(s.endswith(".tmp") for s in segs):
                continue
            cat = "state" if rel in {"research/run_state.json", "research/handoff_resume.md", "research/brief.md"} else (
                segs[1] if len(segs) >= 3 and segs[1] in {"raw", "data", "charts", "media", "screenshots"} else "data"
            )
            items.append((path, rel, cat))
    items.extend((p, p.name, "deliverable") for p in out_dir.iterdir() if p.is_file() and p.name.endswith((".html", "_bundle.zip")))
    items.sort(key=lambda item: item[1])
    return items


def _render_handoff_resume(out_dir: Path, state: dict, file_count: int, corpus_obj: dict | None) -> str:
    """Render concise <=40-line GFM resume card without button syntax."""
    one = lambda v: " ".join(str(v or "").splitlines()).strip()
    stage, win, nr = state["current_stage"], state.get("window") or {}, state.get("new_release_window") or {}
    gaps = state.get("open_l1_l2_gaps") or {"L1": [], "L2": []}
    qc = (corpus_obj or {}).get("qc_summary") if isinstance(corpus_obj, dict) else None
    qc_note = f"passed={qc.get('passed')}, 主题材={qc.get('theme_primary_agreement')}, 呈现={qc.get('presentation_agreement')}, Jaccard={qc.get('mean_tag_set_overlap')}" if isinstance(qc, dict) else "未运行或无摘要"
    blockers = [f"{k}={','.join(gaps[k])}" for k in ("L1", "L2") if gaps.get(k)]
    if stage == "stage2_draft_ready":
        blockers.append("待人工文字批复受控词表签字（唯一人工停点）")
    if stage == "delivered" and not state.get("visual_qa_passed"):
        blockers.append("browser_render_verified=false（未完成浏览器视觉目检）")
    next_cmd = _CHECKPOINT_SPECS[stage][4].format(out_dir=one(out_dir), preset=one(state.get("preset") or "pilot"))
    refs = ", ".join(state.get("next_mandatory_references") or []) or "无"
    lines = [
        "# 续跑状态卡（只读）", "",
        f"## 1. 报告位置与当前阶段\n- 报告目录：`{one(out_dir)}`\n- 预设与阶段：`{one(state.get('preset'))}` / `{one(stage)}` | 最近校验（UTC）：`{one(state.get('last_verified_at'))}`", "",
        f"## 2. 已锁定窗口与门禁状态\n- 市场窗口：`{one(win.get('start'))}` ~ `{one(win.get('end'))}`（共 {len(state.get('months') or [])} 个月） | 新品滚动窗：`{one(nr.get('start'))}` ~ `{one(nr.get('end'))}`\n- 门禁：`vocab_signed_off={state.get('vocab_signed_off')}`，`blind_qc_passed={state.get('blind_qc_passed')}`，`visual_qa_passed={state.get('visual_qa_passed')}`\n- 盲测一致率摘要：{qc_note}", "",
        f"## 3. 产物清单指针\n- 产物清单：`research/data/packet_manifest.json`（预计追踪 {file_count} 项文件，完整路径与 SHA-256 见清单）", "",
        f"## 4. 下一阶段与建议命令\n- 下一阶段：`{state.get('next_stage')}` | 必读参考：`{refs}`\n- 建议首条命令：`{next_cmd}`", "",
        f"## 5. 禁重跑步骤与当前阻塞项\n- 已完成步骤（禁重跑）：`{', '.join(state.get('completed_steps') or [])}`\n- 当前阻塞项：{'；'.join(blockers) if blockers else '无'}",
    ]
    return "\n".join("\n".join(lines).splitlines()[:40]) + "\n"


def cmd_checkpoint(args) -> int:
    """Validate stage outputs and write run_state.json, handoff_resume.md, and packet_manifest.json in order."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not str(args.out_dir or "").strip():
        raise ValueError("--out-dir cannot be empty")
    workspace_cfg, _, _ = load_configs()
    out_dir = validate_out_dir(Path(args.out_dir), workspace_cfg)
    state_path = out_dir / "research/run_state.json"
    if not state_path.is_file():
        raise ValueError(f"missing run_state.json (run init-run first): {state_path}")
    run_state = read(state_path)
    if not isinstance(run_state, dict):
        raise ValueError("research/run_state.json must be a JSON object")

    stage, gate_results = args.stage, _parse_gate_summary(args.gate_summary)
    delivery_obj, corpus_obj = _validate_stage_artifacts(out_dir, stage)
    open_gaps = _extract_open_l1_l2_gaps(out_dir)
    _, next_stage, refs, flags, _ = _CHECKPOINT_SPECS[stage]
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed = list(dict.fromkeys(s for s in (run_state.get("completed_steps") or []) if isinstance(s, str) and s))
    if stage not in completed:
        completed.append(stage)
    new_state = {**run_state, "current_stage": stage, "completed_steps": completed, "next_stage": next_stage,
                 "next_mandatory_references": list(refs), "open_l1_l2_gaps": open_gaps,
                 "last_verified_at": now_utc, "updated_at": now_utc, **flags}
    if stage == "delivered":
        new_state["visual_qa_passed"] = bool(delivery_obj.get("browser_render_verified", False))

    # 1 -> 2 -> 3 write order so packet_manifest.json hashes the final state and resume card.
    write(state_path, new_state)
    predicted = len({rel for _, rel, _ in _scan_manifest_paths(out_dir)} | {"research/run_state.json", "research/handoff_resume.md"})
    resume_text = _render_handoff_resume(out_dir, new_state, predicted, corpus_obj)
    resume_path = out_dir / "research/handoff_resume.md"
    resume_tmp = resume_path.with_name(resume_path.name + ".tmp")
    resume_tmp.write_text(resume_text, encoding="utf-8")
    resume_tmp.replace(resume_path)

    files = [{"rel_path": rel, "bytes": p.stat().st_size, "sha256": digest(p.read_bytes()), "category": cat} for p, rel, cat in _scan_manifest_paths(out_dir)]
    manifest_path = out_dir / "research/data/packet_manifest.json"
    write(manifest_path, {"schema_version": "1.0.0", "generated_at": now_utc, "stage": stage, "preset": new_state.get("preset"), "file_count": len(files), "files": files, "gate_results": gate_results})
    print(json.dumps({"status": "checkpointed", "stage": stage, "run_state": str(state_path), "packet_manifest": str(manifest_path),
                      "handoff_resume": str(resume_path), "file_count": len(files), "resume_lines": len(resume_text.splitlines()), "open_l1_l2_gaps": open_gaps}, ensure_ascii=False))
    return 0


def _read_skill_versions(errors: list[str]) -> tuple[str | None, str | None]:
    """Parse SKILL.md version and optional .skill-version."""
    skill_ver = platform_ver = None
    try:
        m = re.search(r"当前版本：\s*(v?\d+(?:\.\d+){1,3})", (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8-sig", errors="replace"))
        skill_ver = m.group(1) if m else None
        if not skill_ver:
            errors.append("SKILL.md missing '当前版本：'")
    except OSError as exc:
        errors.append(f"failed to read SKILL.md: {exc}")
    sv_path = SKILL_ROOT / ".skill-version"
    if sv_path.is_file():
        try:
            lines = sv_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            platform_ver = (lines[0].strip() or None) if lines else None
        except OSError as exc:
            errors.append(f"failed to read .skill-version: {exc}")
    return skill_ver, platform_ver


def _audit_package(errors: list[str], warnings: list[str]) -> tuple[dict, dict]:
    """Audit skill source files, configs, bytecode presence and entry imports."""
    ignored, source_count, pycache_present = [], 0, False
    for path in sorted(SKILL_ROOT.rglob("*")):
        rel = path.relative_to(SKILL_ROOT)
        if path.is_dir():
            pycache_present |= path.name == "__pycache__"
        elif path.is_file():
            is_bc = "__pycache__" in rel.parts or path.suffix == ".pyc"
            if is_bc or path.name == ".skill-version":
                pycache_present |= is_bc
                ignored.append(rel.as_posix())
            else:
                source_count += 1
    if source_count != 25:
        errors.append(f"expected 25 source files, found {source_count}")
    if pycache_present:
        warnings.append("__pycache__ or .pyc present in skill directory")
    configs_valid, workspace_cfg = True, {}
    for name in ("workspace.json", "subgenres.json", "tag_blacklist.json"):
        try:
            obj = read(SKILL_ROOT / "configs" / name)
            if not isinstance(obj, (dict, list)) or not obj:
                raise ValueError("empty or invalid JSON root")
            if name == "workspace.json" and isinstance(obj, dict):
                workspace_cfg = obj
        except (OSError, ValueError) as exc:
            configs_valid = False
            errors.append(f"invalid config configs/{name}: {exc}")
    entry_imports_ok, prev_bc = True, sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        importlib.invalidate_caches()
        for mod_name in ("data_pipeline", "tag_qc", "compile_report", "research_tools"):
            try:
                mod = importlib.import_module(mod_name)
                mod_file = getattr(mod, "__file__", None)
                if not mod_file or Path(mod_file).resolve().parent != SCRIPT_DIR.resolve():
                    raise ImportError(f"{mod_name} resolved outside {SCRIPT_DIR}")
            except Exception as exc:
                entry_imports_ok = False
                errors.append(f"entry import failed ({mod_name}): {exc}")
    finally:
        sys.dont_write_bytecode = prev_bc
    return {"source_file_count": source_count, "ignored_files": ignored, "pycache_present": pycache_present,
            "configs_valid": configs_valid, "entry_imports_ok": entry_imports_ok}, workspace_cfg


def _check_out_dir(raw_arg: str | None, workspace_cfg: dict, write_probe: bool, errors: list[str]) -> dict:
    """Validate out_dir boundary and check writability without creating directories."""
    source = "explicit" if raw_arg is not None else "derived_cwd"
    res = {"target_path": raw_arg or "", "source": source, "valid_boundary": False, "writable": False, "write_probe_ran": False}
    if raw_arg is not None and not raw_arg.strip():
        errors.append("--out-dir cannot be empty")
        return res
    subdir = (workspace_cfg.get("paths") or {}).get("default_report_subdir") or "reports"
    raw_out = Path(raw_arg) if raw_arg is not None else Path.cwd() / subdir
    res["target_path"] = str(raw_out)
    try:
        resolved = validate_out_dir(raw_out, workspace_cfg)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        return res
    res["target_path"], res["valid_boundary"] = str(resolved), True
    probe_dir = next((p for p in (resolved, *resolved.parents) if p.exists()), None)
    if probe_dir is None or not probe_dir.is_dir():
        errors.append(f"no writable existing directory for: {resolved}")
        return res
    if not write_probe:
        res["writable"] = os.access(probe_dir, os.W_OK | os.X_OK)
        if not res["writable"]:
            errors.append(f"output directory not writable: {probe_dir}")
        return res
    res["write_probe_ran"], probe_path = True, None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".doctor_probe_", suffix=".tmp", dir=probe_dir, delete=False) as fh:
            probe_path = Path(fh.name)
            fh.write(b"doctor_probe_ok")
        if probe_path.read_bytes() != b"doctor_probe_ok":
            raise OSError("write probe readback mismatch")
        res["writable"] = True
    except OSError as exc:
        errors.append(f"write probe failed in {probe_dir}: {exc}")
    finally:
        try:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
        except OSError as exc:
            res["writable"] = False
            errors.append(f"write probe cleanup failed ({probe_path}): {exc}")
    return res


def _probe_network(warnings: list[str], url: str = "https://www.apple.com/robots.txt", timeout: float = 5.0) -> dict:
    """Probe lightweight public HTTP reachability when --check-network is requested."""
    started = time.monotonic()
    try:
        with urlopen(Request(url, method="HEAD", headers={"User-Agent": "casual-tag-market-research/1.0"}), timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            ok = isinstance(code, int) and 200 <= code < 400
    except HTTPError as exc:
        code, ok = exc.code, False
    except (URLError, OSError, ValueError):
        code, ok = None, False
    if not ok:
        warnings.append(f"network reachability probe failed: status_code={code}")
    return {"ok": ok, "status_code": code, "elapsed_sec": round(time.monotonic() - started, 3)}


def cmd_doctor(args) -> int:
    """Run pure local deterministic self-check for environment, package and out-dir boundary."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    errors, warnings = [], []
    skill_ver, platform_ver = _read_skill_versions(errors)
    try:
        with zipfile.ZipFile(io.BytesIO(), "w") as zf:
            zf.writestr("probe.txt", b"ok")
        stdlib_zipfile_ok = True
    except Exception as exc:
        stdlib_zipfile_ok = False
        errors.append(f"stdlib zipfile check failed: {exc}")
    package_audit, workspace_cfg = _audit_package(errors, warnings)
    out_dir_check = _check_out_dir(args.out_dir, workspace_cfg, args.write_probe, errors)
    network_check = _probe_network(warnings) if args.check_network else None
    payload = {
        "status": "failed" if errors else ("warn" if warnings else "ok"),
        "skill_root": str(SKILL_ROOT), "skill_version": skill_ver, "platform_skill_version": platform_ver,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "stdlib_zipfile_ok": stdlib_zipfile_ok, "pil_available": importlib.util.find_spec("PIL") is not None,
        "package_audit": package_audit, "out_dir_check": out_dir_check,
        "network_check": network_check,
        "agent_level_checks_pending": list(AGENT_LEVEL_CHECKS_PENDING), "errors": errors, "warnings": warnings,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 2 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 1 ST/metadata pipeline for casual-tag-market-research")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-run", help="Initialize report directory, date windows and research/run_state.json")
    p_init.add_argument("--out-dir", required=True, help="Absolute report output directory outside skill dir")
    p_init.add_argument("--preset", choices=("pilot", "full"), default="pilot")
    p_init.add_argument("--window-end", help="Last complete calendar month (YYYY-MM), defaults to the month before run-date")
    p_init.add_argument("--run-date", help="Run date (YYYY-MM-DD), defaults to today")
    p_init.add_argument("--title", default="", help="Optional custom report title")
    p_init.set_defaults(func=cmd_init_run)

    p_oss = sub.add_parser("fetch-oss", help="Download full payload when third-party MCP response has truncated=true")
    p_oss.add_argument("--raw-file", required=True, help="Original truncated raw JSON response path")
    p_oss.add_argument("--out-file", required=True, help="Output path for complete JSON")
    p_oss.add_argument("--timeout", type=int, default=30)
    p_oss.set_defaults(func=cmd_fetch_oss)

    p_coverage = sub.add_parser("check-coverage", help="Check merged ranking against single-subgenre rankings")
    p_coverage.add_argument("--merged", required=True, help="Merged revenue ranking JSON")
    p_coverage.add_argument("--singles", nargs="+", required=True, help="Single-subgenre revenue ranking JSON files")
    p_coverage.add_argument("--tolerance", default="0.005", help="Relative tolerance for the last ranked app")
    p_coverage.add_argument("--output", help="Optional JSON audit output path")
    p_coverage.set_defaults(func=cmd_check_coverage)

    p_build = sub.add_parser("build", help="Normalize Stage 1 raw files into market_input, roster, chart specs and tagging bundle")
    p_build.add_argument("--out-dir", required=True, help="Report directory initialized by init-run")
    p_build.add_argument("--manifest", required=True, help="Path to stage1_manifest.json")
    p_build.add_argument("--preset", choices=("pilot", "full"), default=None)
    p_build.add_argument("--union-audit", help="Optional offline new-release union audit JSON")
    p_build.add_argument("--skip-media-download", action="store_true", help="Skip HTTP icon/screenshot download (use existing local files)")
    p_build.set_defaults(func=cmd_build)

    p_doctor = sub.add_parser("doctor", help="Run local deterministic self-check for environment, package and out-dir boundary")
    p_doctor.add_argument("--out-dir", default=None, help="Explicit output directory outside skill dir")
    p_doctor.add_argument("--write-probe", action="store_true", help="Run write/read/delete temp probe in out dir or nearest ancestor")
    p_doctor.add_argument("--check-network", action="store_true", help="Run lightweight public HTTP reachability check")
    p_doctor.set_defaults(func=cmd_doctor)

    p_ckpt = sub.add_parser("checkpoint", help="Verify stage outputs and write run_state, handoff_resume, and packet_manifest")
    p_ckpt.add_argument("--out-dir", required=True, help="Initialized report output directory outside skill dir")
    p_ckpt.add_argument("--stage", required=True, choices=tuple(_CHECKPOINT_SPECS), help="Completed stage to checkpoint")
    p_ckpt.add_argument("--gate-summary", default=None, help="Optional gate summary JSON string or file path")
    p_ckpt.set_defaults(func=cmd_checkpoint)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({
            "status": "failed", "reason": str(exc),
            "next_action": "按失败原因核对参数、文件路径和输入数据，修正后重跑。",
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

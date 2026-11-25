"""Stage 1 pipeline: run init, OSS truncation fetch, ST/metadata normalization and tagging bundle assembly.

Pure Python standard library. No third-party packages required.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
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
    win = build_window_spec(
        args.window_end,
        total_months=int(gdef.get("window_complete_months", 6)),
        recent_months=int(gdef.get("recent_growth_months", 3)),
        lookback_months=int(gdef.get("new_release_lookback_months", 12)),
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
        f"二合品类题材与呈现 Tag 市场研究看板（{win['months'][0]} 至 {win['months'][-1]}）"
        if args.preset == "pilot"
        else f"休闲游戏五品类题材与呈现 Tag 市场研究看板（{win['months'][0]} 至 {win['months'][-1]}）"
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
        "new_release_months": win["new_release_months"],
        "new_release_cutoff_date": win["new_release_cutoff_date"],
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
                      "window": win["window"], "new_release_months": win["new_release_months"]}, ensure_ascii=False))
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


def _load_product_series(manifest, out_dir, window, win_months, retrieved_at, st_sources):
    monthly = {metric: {} for metric in ("revenue", "downloads")}
    source_ids = {metric: {} for metric in monthly}
    first_positive = {}
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
                val = _st_metric_value(row, metric)
                if uid and day and day <= window["end"] and val is not None and val > 0:
                    first_positive[uid] = min(day, first_positive.get(uid, day))
    return monthly, source_ids, first_positive


def _load_union_audit(path, out_dir, sg_id, window, monthly, source_ids, retrieved_at, st_sources):
    abs_path, audit = _load_checked_raw(out_dir, path)
    expected = {"subgenre_id": sg_id, "window_start": window["start"], "window_end": window["end"]}
    for key, value in expected.items():
        if key in audit and audit[key] != value:
            raise ValueError(f"union_audit {key}={audit[key]!r}; expected {value!r}")
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

    complete = [c for c in candidates if not c["exclusion_reason"]]
    revenue = sorted((c for c in complete if c["rev_delta_3m_usd"] > 0), key=revenue_key)
    if rule == "rev_delta_5":
        for candidate in complete:
            if candidate["rev_delta_3m_usd"] <= 0:
                candidate["exclusion_reason"] = "近段净收入增量不大于零"
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

    # 2. Subgenre market size + merge_combined pre-aggregation
    sub_market_files = manifest.get("subgenre_market_size", {})
    sub_monthly_cache: dict[tuple[str, str], dict[str, Decimal]] = {}
    sub_source_ids: dict[tuple[str, str], str] = {}
    ref_subgenres = list(preset_cfg["market_overview_reference_subgenres"])
    for sg_id in ref_subgenres:
        sg_cfg = subgenres_cfg["subgenres"][sg_id]
        sg_files = sub_market_files.get(sg_id, {})
        allowed = set(sg_cfg["st_filter"].get("market_size_target_values") or []) or None
        excluded = {e["value"] for e in sg_cfg.get("excluded_sub_genres", [])} or None
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
            monthly = _extract_market_monthly(raw_obj, metric, target_field="game_sub_genre",
                                              allowed_values=allowed, excluded_values=excluded)
            sub_monthly_cache[(sg_id, metric)] = monthly
            for ym in win_months:
                val = monthly.get(ym)
                records.append(_make_st_record(f"rec_{sg_id}_{metric[:3]}_{ym.replace('-', '')}",
                                               "category", sg_id, metric, ym, val, [src_id], geo_label))
                if val is not None:
                    entity_monthly_avail.setdefault(("category", sg_id, metric), set()).add(ym)

    if preset_cfg.get("include_merge_combined_row", True):
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
    monthly, product_source_ids, first_positive = _load_product_series(
        manifest, out_dir, window, win_months, retrieved_at, st_sources,
    )
    union_files = manifest.get("union_audit") or {}
    union_path = args.union_audit or (union_files if isinstance(union_files, str) else None)
    if union_path:
        resolved_path, union = _load_checked_raw(out_dir, union_path)
        union_files = {union.get("subgenre_id", "merge_2"): str(resolved_path)}
    union_candidates = {
        sg_id: _load_union_audit(path, out_dir, sg_id, window, monthly, product_source_ids, retrieved_at, st_sources)
        for sg_id, path in union_files.items() if sg_id in enabled_sgs
    }
    yl_info_map = manifest.get("yilan_app_info") or {}
    new_release_audit = {}

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
        if sg_id in union_candidates:
            for row in union_candidates[sg_id]:
                stage = row.get("release_stage", "soft_launch" if row.get("soft_launch_status") == "verified" else "global")
                seen_new_uids[str(row["unified_app_id"])] = (row, stage)
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
                for row in rows[:20]:
                    uid = str(row.get("unified_app_id") or "")
                    if uid:
                        prior_row = seen_new_uids.get(uid, ({}, "global"))[0]
                        seen_new_uids[uid] = ({**row, **{k: v for k, v in prior_row.items() if v}}, "global")
            for row in sg_new.get("soft_launch_resolved") or []:
                uid = str(row.get("unified_app_id") or "")
                if uid:
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
            if not launch_date:
                reason = "excluded_launch_unverified"
                print(f"{sg_id} {uid}: {reason}（ST 日序列与并集审计均无可用首发日）", file=sys.stderr)
            elif launch_date < cutoff_date:
                reason = f"最早发行日 {launch_date} 早于近12月门槛 {cutoff_date}"
            elif launch_date > window["end"]:
                reason = f"最早发行日 {launch_date} 晚于统计期"
            candidates.append(_audit_new_candidate(uid, row, launch_date, monthly, prior_months, recent_months, reason))

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
            if candidate["exclusion_reason"]:
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
        {sg_id: sub_monthly_cache.get((sg_id, "revenue"), {}) for sg_id in ref_subgenres}, ["merge_2"],
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

    for uid, prod in roster_by_uid.items():
        eid = prod["entity_id"]
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
        "frozen_roster_6m_revenue_usd": frozen_roster_revenue,
        "market_cr10_denominator_usd": market_cr10_denominator,
        "stats": {
            "total_products": len(roster_by_uid),
            "metadata_matched": matched_meta_count,
            "metadata_match_rate": round(matched_meta_count / max(1, len(roster_by_uid)), 4),
            "yilan_fake_zero_detected_count": fake_zero_count,
        },
    }
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
    labels_map = {p["entity_id"]: p["app_name"][:28] for p in roster_by_uid.values()}
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
        if sg_id == "merge_2" and all(m in entity_monthly_avail.get(("category", "merge_2", "revenue"), set()) for m in win_months):
            dual_spec = {
                "chart": {
                    "id": "merge_2_dual_axis",
                    "title": f"二合品类月度净收入与下载趋势（{win_months[0]}–{win_months[-1]}）",
                    "kind": "market_dual_axis",
                    "selectors": [
                        {"entity_type": "category", "entity_id": "merge_2", "country": "global", "platform": "unified", "metric": "revenue"},
                        {"entity_type": "category", "entity_id": "merge_2", "country": "global", "platform": "unified", "metric": "downloads"},
                    ],
                },
                "data": "../data/market_result.json",
                "labels": labels_map,
            }
            dp = out_dir / "research/charts/merge_2_dual_axis_spec.json"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 1 ST/metadata pipeline for casual-tag-market-research")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-run", help="Initialize report directory, date windows and research/run_state.json")
    p_init.add_argument("--out-dir", required=True, help="Absolute report output directory outside skill dir")
    p_init.add_argument("--preset", choices=("pilot", "full"), default="pilot")
    p_init.add_argument("--window-end", required=True, help="Last complete calendar month (YYYY-MM)")
    p_init.add_argument("--title", default="", help="Optional custom report title")
    p_init.set_defaults(func=cmd_init_run)

    p_oss = sub.add_parser("fetch-oss", help="Download full payload when third-party MCP response has truncated=true")
    p_oss.add_argument("--raw-file", required=True, help="Original truncated raw JSON response path")
    p_oss.add_argument("--out-file", required=True, help="Output path for complete JSON")
    p_oss.add_argument("--timeout", type=int, default=30)
    p_oss.set_defaults(func=cmd_fetch_oss)

    p_build = sub.add_parser("build", help="Normalize Stage 1 raw files into market_input, roster, chart specs and tagging bundle")
    p_build.add_argument("--out-dir", required=True, help="Report directory initialized by init-run")
    p_build.add_argument("--manifest", required=True, help="Path to stage1_manifest.json")
    p_build.add_argument("--preset", choices=("pilot", "full"), default=None)
    p_build.add_argument("--union-audit", help="Optional offline new-release union audit JSON")
    p_build.add_argument("--skip-media-download", action="store_true", help="Skip HTTP icon/screenshot download (use existing local files)")
    p_build.set_defaults(func=cmd_build)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

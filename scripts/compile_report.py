"""Stage 4 compiler: validates cross-stage JSONs, compiles 7-section dashboard template,
runs desensitization scans and packs _bundle.zip. Pure Python standard library.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import zipfile

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import ident, read, write

APPROVED_INLINE_JS = """(function(){
  document.documentElement.classList.add('js-enabled');
  var activeTag = '';
  function applyTagFilter(tagId){
    activeTag = (activeTag === tagId) ? '' : tagId;
    var cards = document.querySelectorAll('.product-card[data-tags]');
    var shown = 0;
    for(var i=0;i<cards.length;i++){
      var list = (cards[i].getAttribute('data-tags') || '').split(',');
      var match = !activeTag || list.indexOf(activeTag) !== -1;
      cards[i].classList.toggle('is-filtered-out', !match);
      cards[i].classList.toggle('is-filtered-hit', !!activeTag && match);
      if(match){ shown++; }
    }
    var btns = document.querySelectorAll('.js-tag-filter');
    for(var j=0;j<btns.length;j++){
      btns[j].classList.toggle('is-active', btns[j].getAttribute('data-tag-id') === activeTag);
    }
    var st = document.getElementById('js-filter-status');
    if(st){
      st.textContent = activeTag ? ('已按标签筛选命中 ' + shown + ' 款产品（再次点击清除）') : '全部产品可见';
    }
  }
  var fbtns = document.querySelectorAll('.js-tag-filter');
  for(var i=0;i<fbtns.length;i++){
    fbtns[i].addEventListener('click', function(){ applyTagFilter(this.getAttribute('data-tag-id')); });
  }
  var clearBtn = document.getElementById('js-filter-clear');
  if(clearBtn){ clearBtn.addEventListener('click', function(){ applyTagFilter(''); }); }
  var headers = document.querySelectorAll('table.sortable-table th[data-sort-col]');
  for(var h=0;h<headers.length;h++){
    headers[h].addEventListener('click', function(){
      var col = parseInt(this.getAttribute('data-sort-col'), 10);
      var asc = this.getAttribute('data-sort-dir') !== 'asc';
      this.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');
      var tbody = this.closest('table').querySelector('tbody');
      if(!tbody){ return; }
      var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
      rows.sort(function(a, b){
        var ac = a.children[col], bc = b.children[col];
        var av = ac ? (ac.getAttribute('data-sort-val') || ac.textContent || '') : '';
        var bv = bc ? (bc.getAttribute('data-sort-val') || bc.textContent || '') : '';
        var an = parseFloat(av), bn = parseFloat(bv);
        if(!isNaN(an) && !isNaN(bn)){ return asc ? (an - bn) : (bn - an); }
        return asc ? av.localeCompare(bv) : bv.localeCompare(av);
      });
      for(var r=0;r<rows.length;r++){ tbody.appendChild(rows[r]); }
    });
  }
  var expBtn = document.getElementById('js-expand-all');
  if(expBtn){
    expBtn.addEventListener('click', function(){
      var all = document.querySelectorAll('main details');
      for(var d=0;d<all.length;d++){ all[d].open = true; }
    });
  }
  var colBtn = document.getElementById('js-collapse-all');
  if(colBtn){
    colBtn.addEventListener('click', function(){
      var all = document.querySelectorAll('main details');
      for(var d=0;d<all.length;d++){ all[d].open = false; }
    });
  }
})();"""


def load_configs():
    cfg_dir = SKILL_ROOT / "configs"
    return (
        read(cfg_dir / "workspace.json"),
        read(cfg_dir / "subgenres.json"),
        read(cfg_dir / "tag_blacklist.json"),
    )


def esc(value) -> str:
    """HTML escape AND shield literal '[[' / 'url(' in user content from html_delivery token checks."""
    text = escape(str(value if value is not None else ""), quote=True)
    text = text.replace("[[", "&#91;&#91;")
    text = re.sub(r"(?i)\burl\s*\(", "url&#40;", text)
    return text


def strip_internal_keys(obj, prefix: str = "_internal"):
    if isinstance(obj, dict):
        return {k: strip_internal_keys(v, prefix) for k, v in obj.items() if not str(k).startswith(prefix)}
    if isinstance(obj, list):
        return [strip_internal_keys(v, prefix) for v in obj]
    return obj


def scan_forbidden_patterns(text: str, patterns: list[str]) -> list[dict]:
    hits = []
    lower = text.lower()
    for pat in patterns:
        p_low = pat.lower()
        idx = lower.find(p_low)
        if idx != -1:
            snippet = text[max(0, idx - 24) : min(len(text), idx + len(pat) + 24)].replace("\n", " ")
            hits.append({"pattern": pat, "snippet": snippet})
    return hits


class AnchorChecker(HTMLParser):
    def __init__(self, html_text: str):
        super().__init__()
        self.ids, self.hrefs = set(), []
        self.dup_ids = []
        self.feed(html_text)
        self.close()

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            if d["id"] in self.ids:
                self.dup_ids.append(d["id"])
            self.ids.add(d["id"])
        if tag == "a" and (d.get("href") or "").startswith("#"):
            self.hrefs.append(d["href"][1:])


def sanitize_source_record(src: dict, workspace_cfg: dict) -> dict:
    pol = workspace_cfg["sanitization_policy"]
    prov_map = pol["provider_display_map"]
    strip_fields = set(pol["strip_source_card_fields"])
    strip_params = set(pol["strip_param_keys"])

    clean = strip_internal_keys(deepcopy(src), pol.get("strip_json_keys_prefixed_with", "_internal"))
    for k in strip_fields:
        clean.pop(k, None)
    raw_provider = str(clean.get("provider") or "公开网页资料")
    clean["provider"] = prov_map.get(raw_provider, raw_provider)
    if isinstance(clean.get("params"), dict):
        clean["params"] = {k: v for k, v in clean["params"].items() if k not in strip_params}
    if "raw_file" in clean and isinstance(clean["raw_file"], str):
        rf = clean["raw_file"].replace("\\", "/")
        if "research/" in rf:
            rf = "research/" + rf.split("research/", 1)[1]
        elif rf.startswith("/") or re.match(r"^[A-Za-z]:/", rf):
            rf = Path(rf).name
        clean["raw_file"] = rf
    return clean


def metric_token(pointer: str, match: dict, *, digits: int = 2, divisor: int | float = 1, suffix: str = "") -> str:
    payload = {"result": "market", "json_pointer": pointer, "match": match, "digits": digits}
    if divisor != 1:
        payload["divisor"] = divisor
    if suffix:
        payload["suffix"] = suffix
    return "[[metric " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "]]"


class MarketIndex:
    """Fast deterministic lookup into market_result.json for [[metric]] token emission."""

    def __init__(self, market_result: dict):
        self.totals = {}
        self.derived = {}
        self.contrib_delta = {}
        self.raw = market_result
        for idx, row in enumerate(market_result.get("totals", [])):
            self.totals[(row.get("entity_type"), row.get("entity_id"), row.get("metric"))] = (idx, row)
        for idx, row in enumerate(market_result.get("derived", [])):
            rid = row.get("id")
            if rid:
                self.derived[rid] = (idx, row)
            if row.get("operation") == "contribution_delta":
                self.contrib_delta[(row.get("entity_id"), row.get("metric"))] = (idx, row)

    def total_tok(self, etype: str, eid: str, metric: str, *, digits: int = 2, divisor=1, suffix: str = "") -> tuple[str, float | None]:
        hit = self.totals.get((etype, eid, metric))
        if not hit or hit[1].get("value") is None:
            return '<span class="missing" data-status="missing">未取得</span>', None
        idx, row = hit
        tok = metric_token(f"/totals/{idx}", {"entity_id": eid, "metric": metric}, digits=digits, divisor=divisor, suffix=suffix)
        return tok, float(row["value"])

    def derived_tok(self, calc_id: str, *, digits: int = 2, divisor=1, suffix: str = "") -> tuple[str, float | None]:
        hit = self.derived.get(calc_id)
        if not hit or hit[1].get("value") is None:
            return '<span class="missing" data-status="missing">不可计算</span>', None
        idx, row = hit
        tok = metric_token(f"/derived/{idx}", {"id": calc_id}, digits=digits, divisor=divisor, suffix=suffix)
        return tok, float(row["value"])

    def delta_tok(self, eid: str, metric: str, *, digits: int = 2, divisor=1, suffix: str = "") -> tuple[str, float | None]:
        hit = self.contrib_delta.get((eid, metric))
        if not hit or hit[1].get("value") is None:
            return '<span class="missing" data-status="missing">未满基期</span>', None
        idx, row = hit
        tok = metric_token(f"/derived/{idx}", {"id": row["id"]}, digits=digits, divisor=divisor, suffix=suffix)
        return tok, float(row["value"])


GAP_COLUMNS = (
    ("gap_id", "缺项编号"),
    ("impact_level", "影响等级"),
    ("missing_evidence", "缺失证据"),
    ("affected_tables", "受影响表格"),
    ("affected_claim_types", "受影响判断"),
    ("allowed_release_scope", "可发布范围"),
    ("release_condition", "放行条件"),
    ("status", "状态"),
)


def _load_gap_register(data_dir):
    path = data_dir / "gap_register.json"
    if not path.is_file():
        return []
    data = read(path)
    gaps = data.get("gaps") if isinstance(data, dict) else data
    if not isinstance(gaps, list) or any(
        not isinstance(gap, dict) or gap.get("impact_level") not in {"L1", "L2", "L3"} for gap in gaps
    ):
        raise ValueError("gap_register.json must contain a gaps array with L1, L2 or L3 entries")
    return gaps


def _open_gap_ids(gaps, level):
    return [str(gap.get("gap_id") or "未编号") for gap in gaps if gap["impact_level"] == level
            and str(gap.get("status") or "").strip().lower() not in {"closed", "resolved", "已关闭", "已解决"}]


def _gap_cell(value):
    if isinstance(value, list):
        return "、".join(esc(item) for item in value) or "—"
    if isinstance(value, dict):
        return esc(json.dumps(value, ensure_ascii=False))
    return esc(value) if value not in (None, "") else "—"


def _film_tag_id(film):
    return film.get("anchor_id") or film.get("linked_tag_id") or film.get("anchor_tag_id") or film.get("tag_id")


def _opportunity_placeholder(preset_cfg, blocked_l2):
    condition_labels = {
        "all_subgenres_zero_missing_months": "五品类月序列零缺月",
        "blind_qc_passed": "盲态重测达标",
        "no_unresolved_low_confidence": "低置信项全部裁决",
        "every_card_has_evidence": "每张机会卡均有证据",
    }
    conditions = preset_cfg.get("opportunity_enable_conditions") or {}
    threshold = "、".join(label for key, label in condition_labels.items() if conditions.get(key, True))
    reason = "L2 缺项未关闭，机会卡与矩阵信号已停用。" if blocked_l2 else "机会卡待启用。"
    return (
        f'<div class="scope-box"><h3>{reason}</h3><dl>'
        '<dt>判据</dt><dd>二合池无命中，其他品类至少 2 款且近 6 个月净收入合计至少 2,000 万美元，'
        '或有至少 1 款已验证高增新品。另核对呈现差异、融合玩法及新品验证结果。</dd>'
        '<dt>所需数据</dt><dd>五品类月序列、受控打标结果与逐卡证据。</dd>'
        f'<dt>启用阈值</dt><dd>{esc(threshold)}须同时满足。</dd>'
        '</dl></div>'
    )


def validate_stage3_inputs(roster: dict, corpus: dict, matrix: dict, opps: dict, films: dict,
                           workspace_cfg: dict, *, preset: str) -> list[str]:
    errors = []
    if corpus.get("status") != "verified" or not (corpus.get("qc_summary") or {}).get("passed"):
        errors.append("tagged_corpus.json is not verified/passed")

    roster_uids = {str(p["unified_app_id"]) for p in roster.get("products", [])}
    corpus_uids = {str(p["unified_app_id"]) for p in corpus.get("products", [])}
    if roster_uids - corpus_uids:
        errors.append(f"products missing from tagged_corpus: {sorted(roster_uids - corpus_uids)}")

    vocab_ids = {v["tag_id"] for v in corpus.get("vocab", [])}
    for p in corpus.get("products", []):
        for t in p.get("tags", []):
            if t.get("tag_id") not in vocab_ids:
                errors.append(f"product {p.get('app_name')} uses unknown tag_id {t.get('tag_id')}")

    opp_cards = opps.get("cards", [])
    film_list = films.get("films", [])
    film_sources = films.get("sources", {})
    opp_ids = {c.get("opp_id") for c in opp_cards if c.get("opp_id")}
    film_ids = {f.get("film_id") for f in film_list if f.get("film_id")}

    req_opp_fields = ("opp_id", "title", "opportunity_type", "target_tag_ids", "supply_gap_metrics",
                      "new_release_validation", "merge2_fit_conditions", "counterexamples_and_costs",
                      "min_validation_actions", "linked_film_ids")
    for card in opp_cards:
        oid = card.get("opp_id", "unknown_opp")
        for f in req_opp_fields:
            if not card.get(f):
                errors.append(f"opportunity {oid}: missing required field '{f}'")
        for tid in card.get("target_tag_ids", []):
            if tid not in vocab_ids:
                errors.append(f"opportunity {oid}: unknown tag_id '{tid}'")
        for fid in card.get("linked_film_ids", []):
            if fid not in film_ids:
                errors.append(f"opportunity {oid}: linked_film_id '{fid}' not found in film_refs.json")

    fpol = workspace_cfg["film_reference_policy"]
    min_p, max_p = int(fpol["paragraphs_per_film"]["min"]), int(fpol["paragraphs_per_film"]["max"])
    forbid_film = [x.lower() for x in fpol["forbidden_film_fields"]]

    for film in film_list:
        fid = film.get("film_id", "unknown_film")
        if preset == "pilot" and film.get("anchor_type") == "tag_fact":
            tag_id = _film_tag_id(film)
            if tag_id not in vocab_ids:
                errors.append(f"film {fid}: anchor tag_id '{tag_id}' missing from tagged_corpus")
        elif film.get("linked_opportunity_id") not in opp_ids:
            errors.append(f"film {fid}: linked_opportunity_id '{film.get('linked_opportunity_id')}' missing from opportunities.json")
        paras = film.get("plot_paragraphs")
        if not isinstance(paras, list) or not (min_p <= len(paras) <= max_p):
            errors.append(f"film {fid}: plot_paragraphs must have {min_p}-{max_p} full paragraphs")
        elif any(len(str(p).strip()) < 40 for p in paras):
            errors.append(f"film {fid}: plot paragraph too short (<40 chars); write complete plot narrative")

        # Forbidden film fields check (keys & content labels)
        for k in film.keys():
            if str(k).lower() in forbid_film:
                errors.append(f"film {fid}: forbidden key '{k}'")
        combined_text = json.dumps(film, ensure_ascii=False).lower()
        for bad in ("演员", "主演", "上映时间", "上映年份", "目标受众", "受众人群"):
            if bad in combined_text:
                errors.append(f"film {fid}: forbidden film field label '{bad}' detected")

        # Anti-IMDb-only source check
        ev_ids = film.get("evidence_source_ids") or []
        if not ev_ids:
            errors.append(f"film {fid}: missing evidence_source_ids")
        elif fpol.get("forbid_short_imdb_synopsis_only", True):
            hosts = []
            for sid in ev_ids:
                url = (film_sources.get(sid) or {}).get("url", "")
                hosts.append(urlsplit(url).netloc.lower())
            if hosts and all("imdb.com" in h for h in hosts if h):
                errors.append(f"film {fid}: cannot rely solely on IMDb synopsis; provide Wikipedia/Fandom/long review")

    # Matrix tag check
    for section_key in ("theme_rows", "presentation_rows"):
        for row in matrix.get(section_key, []) or []:
            if row.get("tag_id") and row["tag_id"] not in vocab_ids:
                errors.append(f"supply_matrix {section_key}: unknown tag_id '{row['tag_id']}'")

    return errors


def _build_default_matrix(roster: dict, corpus: dict, market_idx: MarketIndex,
                          subgenres_cfg: dict, preset: str) -> dict:
    """Fallback matrix computation if supply_matrix.json omits rows."""
    vocab_map = {v["tag_id"]: v for v in corpus["vocab"]}
    prod_map = {str(p["unified_app_id"]): p for p in roster["products"]}
    enabled_sgs = subgenres_cfg["presets"][preset]["enabled_subgenres"]

    rows_by_axis = {"theme_primary": [], "presentation": []}
    pair_counts = defaultdict(list)

    tag_hits = defaultdict(lambda: defaultdict(list))
    for tp in corpus["products"]:
        uid = str(tp["unified_app_id"])
        rp = prod_map.get(uid)
        if not rp:
            continue
        sg = rp["primary_subgenre"]
        t_primary, t_pres = None, None
        for t in tp.get("tags", []):
            tid = t["tag_id"]
            if t.get("slot") == "theme_primary":
                t_primary = tid
            elif t.get("slot") == "presentation":
                t_pres = tid
            tag_hits[tid][sg].append(rp)
        if t_primary and t_pres:
            pair_counts[(t_primary, t_pres)].append(rp)

    for v in corpus["vocab"]:
        axis = v["axis"]
        if axis not in rows_by_axis:
            continue
        tid = v["tag_id"]
        cells = {}
        total_cnt = 0
        other_rev = 0.0
        for sg in enabled_sgs:
            prods = tag_hits[tid].get(sg, [])
            rev_sum = 0.0
            for pr in prods:
                _, rv = market_idx.total_tok("product", pr["entity_id"], "revenue")
                if rv is not None:
                    rev_sum += rv
            cnt = len(prods)
            total_cnt += cnt
            if sg != "merge_2":
                other_rev += rev_sum
            heat = "absent" if cnt == 0 else "singleton" if cnt == 1 else "moderate" if cnt <= 3 else "crowded"
            cells[sg] = {
                "count": cnt,
                "revenue_6m_usd": round(rev_sum, 2),
                "exemplars": [{"unified_app_id": p["unified_app_id"], "app_name": p["app_name"]} for p in prods[:2]],
                "heat_level": heat,
            }
        m2_cnt = (cells.get("merge_2") or {}).get("count", 0)
        other_cnt = total_cnt - m2_cnt
        if m2_cnt >= 3:
            sig, sig_lbl = "crowded", "二合红海"
        elif preset == "full" and m2_cnt == 0 and other_cnt >= 2 and other_rev >= 20_000_000:
            sig, sig_lbl = "spillover_gap", "外溢空白"
        elif m2_cnt == 1 and (other_cnt >= 2 or preset == "pilot"):
            sig, sig_lbl = "low_supply", "二合低供给"
        else:
            sig, sig_lbl = "niche_watch", "小众观察"
        rows_by_axis[axis].append({
            "tag_id": tid, "label_zh": v["label_zh"], "total_count": total_cnt,
            "cells": cells, "signal_code": sig, "signal_label": sig_lbl,
        })

    for ax in rows_by_axis:
        rows_by_axis[ax].sort(key=lambda r: (-r["total_count"], r["tag_id"]))

    pairs = []
    for (tp_id, pr_id), plist in sorted(pair_counts.items(), key=lambda kv: -len(kv[1])):
        pairs.append({
            "theme_tag_id": tp_id,
            "theme_label": vocab_map[tp_id]["label_zh"],
            "presentation_tag_id": pr_id,
            "presentation_label": vocab_map[pr_id]["label_zh"],
            "count": len(plist),
            "products": [{"unified_app_id": p["unified_app_id"], "app_name": p["app_name"]} for p in plist],
        })
    return {
        "mode": "single_subgenre_theme_x_presentation" if preset == "pilot" else "cross_subgenre_supply_gap_plus_pairs",
        "theme_rows": rows_by_axis["theme_primary"],
        "presentation_rows": rows_by_axis["presentation"],
        "pairs": pairs,
    }


def _render_tag_pill(tag_id: str, label_zh: str, axis: str, parent_label: str | None = None) -> str:
    if parent_label:
        label_zh = f"{parent_label} › {label_zh}"
    return f'<a class="tag-pill axis-{esc(axis)}" href="#tag-{esc(tag_id)}" data-tag-id="{esc(tag_id)}">{esc(label_zh)}</a>'


def cmd_build(args) -> int:
    workspace_cfg, subgenres_cfg, blacklist_cfg = load_configs()
    out_dir = Path(args.out_dir).resolve()
    run_state = read(out_dir / "research/run_state.json")
    preset = args.preset or run_state["preset"]
    preset_cfg = subgenres_cfg["presets"][preset]
    script_mode = args.script_mode or preset_cfg["html_script_mode"]

    data_dir = out_dir / "research/data"
    gaps = _load_gap_register(data_dir)
    open_l1 = _open_gap_ids(gaps, "L1")
    if open_l1:
        raise ValueError(f"unclosed L1 gaps: {', '.join(open_l1)}")
    open_l2 = _open_gap_ids(gaps, "L2")
    opportunities_enabled = bool(preset_cfg.get("enable_opportunity_cards")) and not open_l2
    market_res = read(data_dir / "market_result.json")
    roster = strip_internal_keys(read(out_dir / "research/data/product_roster.json"))
    corpus = strip_internal_keys(read(out_dir / "research/data/tagged_corpus.json"))
    opp_path = data_dir / "opportunities.json"
    film_path = data_dir / "film_refs.json"
    opps = strip_internal_keys(read(opp_path)) if opp_path.is_file() else {}
    films = strip_internal_keys(read(film_path)) if film_path.is_file() else {}
    opps["cards"] = opps.get("cards") or []
    films["films"] = films.get("films") or []
    if open_l2 and opps["cards"]:
        raise ValueError(f"unclosed L2 gaps block opportunity cards and matrix signals: {', '.join(open_l2)}")
    if not opportunities_enabled:
        opps["cards"] = []
        films["films"] = [f for f in films["films"] if f.get("anchor_type") == "tag_fact"]
    matrix_path = out_dir / "research/data/supply_matrix.json"
    matrix = strip_internal_keys(read(matrix_path)) if matrix_path.is_file() else {}
    bundle_path = out_dir / "research/data/tagging_input_bundle.json"
    bundle = strip_internal_keys(read(bundle_path)) if bundle_path.is_file() else {"products": [], "sources": {}}

    midx = MarketIndex(market_res)
    if not matrix.get("theme_rows") and not matrix.get("presentation_rows"):
        matrix = _build_default_matrix(roster, corpus, midx, subgenres_cfg, preset)

    v_errors = validate_stage3_inputs(roster, corpus, matrix, opps, films, workspace_cfg, preset=preset)
    if v_errors:
        raise ValueError("stage3 validation failed: " + "; ".join(v_errors))

    # Merge & sanitize sources for delivery-input.json
    merged_sources = {}
    for src_dict in (bundle.get("sources") or {}, films.get("sources") or {}, market_res.get("sources") or {}):
        for sid, sitem in src_dict.items():
            clean_s = sanitize_source_record(sitem, workspace_cfg)
            if sid in merged_sources and merged_sources[sid] != clean_s:
                # Preserve Sensor Tower authoritative entry if identical ID
                if merged_sources[sid].get("provider") == "Sensor Tower":
                    continue
            merged_sources[sid] = clean_s

    # Collect existing charts & product icons for delivery-input.json
    charts_manifest = {}
    charts_dir = out_dir / "research/charts"
    if charts_dir.is_dir():
        for meta_file in sorted(charts_dir.glob("*.json")):
            if meta_file.name.endswith("_spec.json"):
                continue
            cmeta = read(meta_file)
            cid = cmeta.get("id")
            svg_file = meta_file.with_suffix(".svg")
            if cid and svg_file.is_file() and cmeta.get("status") == "available":
                charts_manifest[cid] = {
                    "file": f"charts/{svg_file.name}",
                    "metadata": f"charts/{meta_file.name}",
                    "result": "market",
                }

    assets_manifest = {}
    for p in roster["products"]:
        if p.get("icon_file") and p.get("icon_mime"):
            abs_icon = out_dir / p["icon_file"]
            if abs_icon.is_file():
                aid = ident(f"icon_{p['entity_id'][:40]}")
                ev_id = p.get("icon_source_id")
                if not ev_id or ev_id not in merged_sources:
                    # Bind to first ST performance source if metadata source absent
                    ev_id = next((k for k, v in merged_sources.items() if v.get("provider") == "Sensor Tower"), None)
                if ev_id:
                    rel_from_research = p["icon_file"].split("research/", 1)[1] if p["icon_file"].startswith("research/") else p["icon_file"]
                    assets_manifest[aid] = {
                        "file": rel_from_research,
                        "mime": p["icon_mime"],
                        "product_id": p["entity_id"],
                        "role": "icon",
                        "caption": f"{p['app_name']} 商店图标",
                        "evidence_ids": [ev_id],
                    }
                    p["_icon_asset_id"] = aid

    # Lookups
    vocab_map = {v["tag_id"]: v for v in corpus["vocab"]}
    parent_labels = {domain["id"]: domain["label_zh"] for domain in blacklist_cfg["primary_domains"]}
    tag_prod_map = {str(p["unified_app_id"]): p for p in corpus["products"]}
    bundle_prod_map = {str(p["unified_app_id"]): p for p in bundle.get("products", [])}
    prod_by_uid = {str(p["unified_app_id"]): p for p in roster["products"]}
    enabled_sgs = list(preset_cfg["enabled_subgenres"])
    all_5_sgs = ["merge_2", "merge_3", "match_3", "simulation", "hybrid_puzzle"]
    ref_sgs = list(preset_cfg["market_overview_reference_subgenres"])
    win = run_state["window"]
    retrieved_at = next((v.get("retrieved_at", "") for v in merged_sources.values() if v.get("retrieved_at")), run_state.get("updated_at", "")[:10])
    title = run_state.get("report_title") or "休闲品类题材与呈现 Tag 市场研究看板"

    def _render_product_tag_pills(tags: list[dict]) -> str:
        pills = []
        for tag in tags:
            tag_id, axis = tag["tag_id"], tag["slot"]
            entry = vocab_map[tag_id]
            parent_label = parent_labels.get(entry.get("parent_domain")) if axis == "theme_primary" else None
            pills.append(_render_tag_pill(tag_id, entry["label_zh"], axis, parent_label))
        return "".join(pills)

    # Helper: best growth subgenre
    best_sg_id, best_sg_qoq = enabled_sgs[0], -1e9
    for sg_id in ref_sgs:
        _, qval = midx.derived_tok(f"{sg_id}_rev_qoq3m")
        if qval is not None and qval > best_sg_qoq:
            best_sg_id, best_sg_qoq = sg_id, qval

    # Sidebar
    sg_nav_links = []
    for sg_id in all_5_sgs:
        sg_name = subgenres_cfg["subgenres"][sg_id]["display_name"]
        badge = "" if sg_id in enabled_sgs else ' <span class="nav-dim">待扩</span>'
        sg_nav_links.append(f'<li><a href="#panel-{esc(sg_id)}">{esc(sg_name)}{badge}</a></li>')

    sidebar_html = f"""<aside class="dash-sidebar" aria-label="看板导航">
  <div class="sidebar-brand">
    <div class="brand-kicker">CASUAL TAG DASHBOARD</div>
    <div class="brand-title">{esc(title)}</div>
    <div class="brand-meta">模式：{esc('二合试点 (pilot)' if preset == 'pilot' else '五品类全量 (full)')}</div>
  </div>
  <nav class="sidebar-nav">
    <ol>
      <li><a href="#sec-overview">0. 执行概览</a></li>
      <li><a href="#sec-market">1. 市场盘面看板</a></li>
      <li><a href="#sec-subgenres">2. 品类分章看板</a>
        <ul class="sub-nav">{''.join(sg_nav_links)}</ul>
      </li>
      <li><a href="#sec-new-apps">3. 高增新品观察</a></li>
      <li><a href="#sec-matrix">4. Tag 供给差与机会</a></li>
      <li><a href="#sec-films">5. 影视剧情借鉴</a></li>
      <li><a href="#sec-appendix">6. 词表与口径附录</a></li>
    </ol>
  </nav>
</aside>"""

    # Section 0: Executive Overview (1-screen summary)
    macro_rev_tok, _ = midx.total_tok("category", "macro_casual", "revenue", digits=2, divisor=1_000_000_000, suffix="B USD")
    macro_dl_tok, _ = midx.total_tok("category", "macro_casual", "downloads", digits=2, divisor=1_000_000_000, suffix="B 次")
    mc_rev_tok, _ = midx.total_tok("category", "merge_combined", "revenue", digits=2, divisor=1_000_000, suffix="M USD")
    best_qoq_tok, _ = midx.derived_tok(f"{best_sg_id}_rev_qoq3m", digits=1, suffix="%")
    best_sg_name = subgenres_cfg["subgenres"][best_sg_id]["display_name"]

    opp_summary_cards = []
    for c in opps["cards"]:
        pills = "".join(_render_tag_pill(tid, vocab_map[tid]["label_zh"], vocab_map[tid]["axis"]) for tid in c["target_tag_ids"])
        opp_summary_cards.append(
            f'<article class="opp-mini-card">'
            f'<div class="opp-mini-head"><span class="badge badge-opp">{esc(c["opportunity_type"])}</span>'
            f'<a class="opp-jump" href="#opp-{esc(c["opp_id"])}">详卡 →</a></div>'
            f'<h3>{esc(c["title"])}</h3>'
            f'<div class="pill-row">{pills}</div>'
            f'<p>{esc(c.get("summary_line") or c["supply_gap_metrics"].get("benchmark_summary", ""))}</p>'
            f'</article>'
        )

    sec0_html = f"""<section id="sec-overview" class="dash-section overview-hero">
  <header class="meta-strip">
    <h1>{esc(title)}</h1>
    <div class="meta-tags">
      <span>品类范围：{esc('、'.join(subgenres_cfg['subgenres'][s]['display_name'] for s in enabled_sgs))}</span>
      <span>统计窗口：{esc(win['start'])} 至 {esc(win['end'])}</span>
      <span>采集日：{esc(retrieved_at)}</span>
      <span>口径：Sensor Tower 全球 IAP 净收入估算（含中国大陆 iOS）</span>
    </div>
  </header>
  <div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-label">Casual 宏观 近 6 个月净收入</div><div class="kpi-val">{macro_rev_tok}</div></div>
    <div class="kpi-card"><div class="kpi-label">Casual 宏观 近 6 个月下载量</div><div class="kpi-val">{macro_dl_tok}</div></div>
    <div class="kpi-card"><div class="kpi-label">合成合计（二合 + 三合）近 6 个月净收入</div><div class="kpi-val">{mc_rev_tok}</div></div>
    <div class="kpi-card"><div class="kpi-label">近 3 月环比领先赛道（{esc(best_sg_name)}）</div><div class="kpi-val">{best_qoq_tok}</div></div>
  </div>
  <div class="overview-takeaway">
    <p>本看板基于 Sensor Tower 全球双端合并估算与受控三轴 Tag 词表（共 {len(corpus['vocab'])} 词，盲态重测主题材一致率 {corpus['qc_summary']['theme_primary_agreement']*100:.1f}%），覆盖 {len(roster['products'])} 款入围产品，沉淀 {len(opps['cards'])} 条立项机会方向与 {len(films['films'])} 部影视长剧情借鉴。</p>
  </div>
  <div class="opp-mini-grid">{''.join(opp_summary_cards)}</div>
</section>"""

    # Section 1: Market Overview
    macro_chart_html = "[[chart macro_dual_axis]]" if "macro_dual_axis" in charts_manifest else '<p class="missing" data-status="missing">宏观双轴趋势图未生成。</p>'
    mkt_rows = []
    for sg_id in all_5_sgs:
        sg_cfg = subgenres_cfg["subgenres"][sg_id]
        if sg_id in ref_sgs:
            r6, r6v = midx.total_tok("category", sg_id, "revenue", digits=2, divisor=1_000_000, suffix="M USD")
            d6, d6v = midx.total_tok("category", sg_id, "downloads", digits=2, divisor=1_000_000, suffix="M")
            qoq, qv = midx.derived_tok(f"{sg_id}_rev_qoq3m", digits=1, suffix="%")
            cr10, crv = midx.derived_tok(f"{sg_id}_cr10_rev", digits=1, suffix="%")
            rpd, rpv = midx.derived_tok(f"{sg_id}_rpd6m", digits=2, suffix=" USD")
            ortho = "（正交维度·不可相加）" if not sg_cfg["additive_with_gameplay_peers"] else ""
            mkt_rows.append(
                f'<tr><td><a href="#panel-{esc(sg_id)}">{esc(sg_cfg["display_name"])}</a>{esc(ortho)}</td>'
                f'<td class="num" data-sort-val="{r6v if r6v is not None else -1}">{r6}</td>'
                f'<td class="num" data-sort-val="{d6v if d6v is not None else -1}">{d6}</td>'
                f'<td class="num" data-sort-val="{qv if qv is not None else -999}">{qoq}</td>'
                f'<td class="num" data-sort-val="{crv if crv is not None else -1}">{cr10}</td>'
                f'<td class="num" data-sort-val="{rpv if rpv is not None else -1}">{rpd}</td></tr>'
            )
        else:
            mkt_rows.append(
                f'<tr class="missing" data-status="missing"><td>{esc(sg_cfg["display_name"])}</td>'
                f'<td colspan="5">待五品类全量扩充</td></tr>'
            )
    # Merge combined benchmark row
    mc_r6, _ = midx.total_tok("category", "merge_combined", "revenue", digits=2, divisor=1_000_000, suffix="M USD")
    mc_d6, _ = midx.total_tok("category", "merge_combined", "downloads", digits=2, divisor=1_000_000, suffix="M")
    mc_qoq, _ = midx.derived_tok("merge_combined_rev_qoq3m", digits=1, suffix="%")
    mc_rpd, _ = midx.derived_tok("merge_combined_rpd6m", digits=2, suffix=" USD")
    mkt_rows.append(
        f'<tr class="row-highlight"><td><strong>合成合计（二合 + 三合）</strong></td>'
        f'<td class="num">{mc_r6}</td><td class="num">{mc_d6}</td><td class="num">{mc_qoq}</td>'
        f'<td class="num">—</td><td class="num">{mc_rpd}</td></tr>'
    )

    sec1_html = f"""<section id="sec-market" class="dash-section">
  <h2>1. 市场盘面看板</h2>
  <div class="chart-box">{macro_chart_html}</div>
  <div class="table-wrap">
    <table class="data-table sortable-table">
      <thead><tr>
        <th data-sort-col="0">研究品类</th><th data-sort-col="1">近 6 个月净收入</th><th data-sort-col="2">近 6 个月下载</th>
        <th data-sort-col="3">近 3 月收入环比</th><th data-sort-col="4">市场 CR10</th><th data-sort-col="5">近似 RPD</th>
      </tr></thead>
      <tbody>{''.join(mkt_rows)}</tbody>
    </table>
  </div>
  <details class="info-fold">
    <summary>查看维度正交声明与统计口径说明</summary>
    <ul>
      <li><strong>维度正交</strong>：混变益智（Hybridcasual × Puzzle）属产品模型 × 题材交集维度，与前四类玩法子类维度正交，<strong>不可与前四列相加求和大盘</strong>。</li>
      <li><strong>合成合计说明</strong>：{esc(subgenres_cfg['macro_benchmarks']['merge_combined_reference']['public_note'])}</li>
      <li><strong>近似 RPD 说明</strong>：{esc(subgenres_cfg['global_st_defaults']['approx_rpd_public_note'])}</li>
    </ul>
  </details>
</section>"""

    # Section 2: Subgenre Panels (Pure CSS Radio Tabs + optional JS toolbar for full)
    js_toolbar = ""
    if script_mode == "whitelisted_inline_js":
        filter_btns = "".join(
            f'<button type="button" class="js-tag-filter axis-{esc(v["axis"])}" data-tag-id="{esc(v["tag_id"])}">{esc(v["label_zh"])}</button>'
            for v in corpus["vocab"]
        )
        js_toolbar = (
            '<div class="dashboard-filter-bar">'
            '<div class="filter-bar-top"><strong>跨品类 Tag 快速筛选</strong>'
            '<span id="js-filter-status">全部产品可见</span>'
            '<div class="filter-actions">'
            '<button type="button" id="js-filter-clear">清除筛选</button>'
            '<button type="button" id="js-expand-all">全部展开详情</button>'
            '<button type="button" id="js-collapse-all">全部折叠详情</button>'
            '</div></div>'
            f'<div class="filter-btn-list">{filter_btns}</div></div>'
        )

    radio_inputs = []
    tab_labels = []
    tab_panels = []
    default_tab = enabled_sgs[0]

    for sg_id in [*all_5_sgs, "all"]:
        checked = " checked" if sg_id == default_tab else ""
        radio_inputs.append(f'<input type="radio" class="tab-radio" name="subgenre-tab" id="tab-radio-{esc(sg_id)}"{checked}>')
        lbl = "全部展开" if sg_id == "all" else subgenres_cfg["subgenres"][sg_id]["display_name"]
        tab_labels.append(f'<label class="tab-label" for="tab-radio-{esc(sg_id)}">{esc(lbl)}</label>')

    for sg_id in all_5_sgs:
        sg_cfg = subgenres_cfg["subgenres"][sg_id]
        if sg_id not in enabled_sgs:
            tab_panels.append(
                f'<div id="panel-{esc(sg_id)}" class="tab-panel panel-{esc(sg_id)} missing" data-status="missing">'
                f'<h3>{esc(sg_cfg["display_name"])}（待五品类全量扩充）</h3>'
                f'<p class="muted">{esc(sg_cfg["public_definition"])}</p></div>'
            )
            continue

        sg_prods = [p for p in roster["products"] if p["primary_subgenre"] == sg_id and ("top10_revenue" in p["roles"] or "download_supplement" in p["roles"] or "whitelist_exception" in p["roles"])]
        sg_prods.sort(key=lambda p: (0 if "top10_revenue" in p["roles"] else 1 if "whitelist_exception" in p["roles"] else 2,
                                     p.get("subgenre_rank") or p.get("download_rank") or 999))
        sg_r6, _ = midx.total_tok("category", sg_id, "revenue", digits=2, divisor=1_000_000, suffix="M USD")
        sg_qoq, _ = midx.derived_tok(f"{sg_id}_rev_qoq3m", digits=1, suffix="%")
        sg_cr10, _ = midx.derived_tok(f"{sg_id}_cr10_rev", digits=1, suffix="%")

        charts_in_panel = []
        if f"{sg_id}_dual_axis" in charts_manifest:
            charts_in_panel.append(f'<div class="chart-box">[[chart {sg_id}_dual_axis]]</div>')
        if f"subgenre_{sg_id}_rank" in charts_manifest:
            charts_in_panel.append(f'<div class="chart-box">[[chart subgenre_{sg_id}_rank]]</div>')

        cards_html = []
        table_rows = []
        for p in sg_prods:
            uid = str(p["unified_app_id"])
            eid = p["entity_id"]
            tp = tag_prod_map[uid]
            bp = bundle_prod_map.get(uid) or {}
            pr_rev, pr_rv = midx.total_tok("product", eid, "revenue", digits=2, divisor=1_000_000, suffix="M USD")
            pr_dl, pr_dv = midx.total_tok("product", eid, "downloads", digits=2, divisor=1_000_000, suffix="M")
            pr_qoq, pr_qv = midx.derived_tok(f"{eid}_rev_qoq3m", digits=1, suffix="%")
            pr_rpd, pr_rpv = midx.derived_tok(f"{eid}_rpd6m", digits=2, suffix=" USD")

            rank_badge = (f"#{p['subgenre_rank']}" if p.get("subgenre_rank") else
                          "特例" if "whitelist_exception" in p["roles"] else
                          f"下载#{p.get('download_rank', '-')}")
            extra_badge = f'<span class="badge badge-warn">{esc(p["public_badge"])}</span>' if p.get("public_badge") else ""
            icon_tok = f'[[image {p["_icon_asset_id"]} {eid}]]' if p.get("_icon_asset_id") else '<div class="icon-placeholder">无图标</div>'

            t_ids = [t["tag_id"] for t in tp["tags"]]
            pills = _render_product_tag_pills(tp["tags"])
            ev_items = []
            for t in tp["tags"]:
                cite_tok = f' [[cite {t["evidence_source_id"]}]]' if t.get("evidence_source_id") in merged_sources else ""
                ev_items.append(
                    f'<li><strong>{esc(vocab_map[t["tag_id"]]["label_zh"])}</strong>'
                    f'（{esc(t["evidence_type"])}）：{esc(t["evidence_quote"])}{cite_tok}</li>'
                )
            excl_html = "".join(f'<li>{esc(x)}</li>' for x in (tp.get("excluded_near_tags") or []))
            ads = ((bp.get("layer3_voices_and_ads") or {}).get("ad_slogans_marketing_only") or [])[:3]
            ad_html = "".join(
                f'<li><span class="badge badge-muted">买量素材·仅辅证</span> {esc(a["slogan"])}'
                + (f' [[cite {a["source_id"]}]]' if a.get("source_id") in merged_sources else "") + '</li>'
                for a in ads
            )
            geo_li = f'<li><strong>地区属性</strong>：{esc(p["geo_note"])}</li>' if p.get("cn_ios_only") else ""
            one_liner = tp["tags"][0]["evidence_quote"][:68] if tp["tags"] else ""

            cards_html.append(
                f'<article id="prod-{esc(eid)}" class="product-card" data-tags="{esc(",".join(t_ids))}">'
                f'<div class="card-top">{icon_tok}'
                f'<div class="card-id"><div class="card-badges"><span class="rank-badge">{esc(rank_badge)}</span>{extra_badge}</div>'
                f'<h4>{esc(p["app_name"])}</h4><div class="card-pub">{esc(p["publisher_name"])}</div></div></div>'
                f'<div class="mini-kpi-row">'
                f'<div><span>近 6 个月净收入</span><strong>{pr_rev}</strong></div>'
                f'<div><span>近 6 个月下载</span><strong>{pr_dl}</strong></div>'
                f'<div><span>近3月环比</span><strong>{pr_qoq}</strong></div>'
                f'<div><span>近似RPD</span><strong>{pr_rpd}</strong></div></div>'
                f'<div class="pill-row">{pills}</div>'
                f'<p class="card-brief">{esc(one_liner)}</p>'
                f'<details class="card-details"><summary>展开 Tag 依据、排除项与产品档案</summary>'
                f'<ul>{"".join(ev_items)}{geo_li}'
                f'<li><strong>最早发行日</strong>：{esc(p.get("publisher_start") or "未提供")}</li></ul>'
                f'<div class="sub-block"><strong>次选排除理由</strong><ul>{excl_html}</ul></div>'
                + (f'<div class="sub-block"><strong>买量辅证</strong><ul>{ad_html}</ul></div>' if ad_html else "")
                + '</details></article>'
            )
            table_rows.append(
                f'<tr><td>{esc(rank_badge)}</td><td><a href="#prod-{esc(eid)}">{esc(p["app_name"])}</a></td>'
                f'<td>{esc(p["publisher_name"])}</td>'
                f'<td class="num" data-sort-val="{pr_rv if pr_rv is not None else -1}">{pr_rev}</td>'
                f'<td class="num" data-sort-val="{pr_dv if pr_dv is not None else -1}">{pr_dl}</td>'
                f'<td class="num" data-sort-val="{pr_qv if pr_qv is not None else -999}">{pr_qoq}</td>'
                f'<td class="num" data-sort-val="{pr_rpv if pr_rpv is not None else -1}">{pr_rpd}</td>'
                f'<td>{pills}</td></tr>'
            )

        tab_panels.append(
            f'<div id="panel-{esc(sg_id)}" class="tab-panel panel-{esc(sg_id)}">'
            f'<div class="subgenre-head"><div><h3>{esc(sg_cfg["display_name"])}</h3>'
            f'<p class="muted">{esc(sg_cfg["public_definition"])}</p></div>'
            f'<div class="sg-kpis"><span>近 6 个月净收入：{sg_r6}</span><span>近3月环比：{sg_qoq}</span><span>市场 CR10：{sg_cr10}</span></div></div>'
            f'{"".join(charts_in_panel)}'
            f'<div class="product-grid">{"".join(cards_html)}</div>'
            f'<details class="info-fold"><summary>查看{esc(sg_cfg["display_name"])}完整数值对比表（{len(table_rows)} 款）</summary>'
            f'<div class="table-wrap"><table class="data-table sortable-table"><thead><tr>'
            f'<th data-sort-col="0">位次</th><th data-sort-col="1">产品</th><th data-sort-col="2">发行商</th>'
            f'<th data-sort-col="3">近 6 个月净收入</th><th data-sort-col="4">近 6 个月下载</th><th data-sort-col="5">近3月环比</th>'
            f'<th data-sort-col="6">近似RPD</th><th>受控Tag</th></tr></thead><tbody>{"".join(table_rows)}</tbody></table></div></details></div>'
        )

    sec2_html = f"""<section id="sec-subgenres" class="dash-section">
  <h2>2. 品类分章看板</h2>
  {js_toolbar}
  <div class="css-tabs">
    {''.join(radio_inputs)}
    <div class="tab-bar">{''.join(tab_labels)}</div>
    <div class="tab-panels">{''.join(tab_panels)}</div>
  </div>
</section>"""

    # Section 3: High-Growth New Releases
    new_prods = sorted(
        (p for p in roster["products"] if p.get("is_new_release")),
        key=lambda p: (enabled_sgs.index(p["primary_subgenre"]), p.get("new_release_rank") or 0),
    )
    new_cards = []
    for p in new_prods:
        uid, eid = str(p["unified_app_id"]), p["entity_id"]
        tp = tag_prod_map[uid]
        rev_d, _ = midx.delta_tok(eid, "revenue", digits=2, divisor=1_000_000, suffix="M USD")
        dl_d, _ = midx.delta_tok(eid, "downloads", digits=2, divisor=1_000_000, suffix="M")
        r3_rev, _ = midx.derived_tok(f"{eid}_rev_r3m", digits=2, divisor=1_000_000, suffix="M USD")
        pills = _render_product_tag_pills(tp["tags"])
        stage_badge = "测试期新品" if p.get("release_stage") == "soft_launch" else "近12月全球首发"
        boundary_badge = '<span class="badge badge-opp">边界席</span>' if p.get("is_boundary_seat") else ""
        launch_date = p.get("effective_launch_date") or p.get("publisher_start") or "近12月内"
        jump = f'<a href="#prod-{esc(eid)}">查看主榜卡</a>' if ("top10_revenue" in p["roles"] or "download_supplement" in p["roles"]) else ""
        new_cards.append(
            f'<article id="new-{esc(eid)}" class="new-app-card">'
            f'<div class="new-head"><strong>{esc(p["app_name"])}</strong>'
            f'<span class="badge">{esc(subgenres_cfg["subgenres"][p["primary_subgenre"]]["display_name"])}</span>'
            f'<span class="badge badge-muted">{esc(stage_badge)}</span>{boundary_badge}{jump}</div>'
            f'<div class="new-metrics"><span>首发日：{esc(launch_date)}</span>'
            f'<span>近3月收入：{r3_rev}</span><span>近3月收入增量：{rev_d}</span><span>近3月下载增量：{dl_d}</span></div>'
            f'<div class="pill-row">{pills}</div></article>'
        )
    excl_rows = "".join(
        f'<tr><td>{esc(e.get("subgenre_id",""))}</td><td>{esc(e.get("app_name",""))}</td>'
        f'<td>{esc(e.get("stage",""))}</td><td>{esc(e.get("reason",""))}</td></tr>'
        for e in (roster.get("excluded_audit") or [])
    )
    fifth_by_subgenre = {p["primary_subgenre"]: p for p in new_prods if p.get("new_release_rank") == 5}
    new_audit = roster.get("new_release_audit") or {}
    sixth_readings = []
    for sg_id in enabled_sgs:
        fifth = fifth_by_subgenre.get(sg_id, {})
        delta = new_audit.get(sg_id, {}).get("sixth_seat_rev_delta_usd")
        if delta is None:
            delta = fifth.get("sixth_seat_rev_delta_usd")
        delta_text = f"{delta:,.2f} USD" if delta is not None else "—"
        boundary_text = {True: "是", False: "否"}.get(fifth.get("is_boundary_seat"), "—")
        label = subgenres_cfg["subgenres"][sg_id]["display_name"]
        sixth_readings.append(f"{esc(label)}净收入增量 {delta_text}（第 5 席为边界席：{boundary_text}）")
    sec3_html = f"""<section id="sec-new-apps" class="dash-section">
  <h2>3. 高增新品观察</h2>
  <p class="scope-pill">筛选口径：ST Release Date (WW) 近 12 月动态枚举 + 主类 6014 + custom_fields_include_unified_apps=false + 最早发行日二次核验</p>
  <div class="new-app-list">{''.join(new_cards) or '<p class="missing" data-status="missing">当前窗口内暂无符合口径的高增真新品。</p>'}</div>
  <details class="info-fold"><summary>查看剔除与复核审计表（{len(roster.get('excluded_audit') or [])} 条）</summary>
    <p><strong>第 6 名实数</strong>：{'；'.join(sixth_readings)}</p>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>品类</th><th>应用名</th><th>阶段</th><th>剔除或复核理由</th></tr></thead>
    <tbody>{excl_rows or '<tr><td colspan="4">无剔除项</td></tr>'}</tbody></table></div>
  </details>
</section>"""

    # Section 4: Supply-Gap Matrix & Opportunity Detail Cards
    def _render_matrix_rows(rows: list[dict], with_signal: bool = True) -> str:
        out = []
        for r in rows:
            tid = r["tag_id"]
            tds = [f'<td>{_render_tag_pill(tid, r.get("label_zh") or vocab_map[tid]["label_zh"], vocab_map[tid]["axis"])}</td>']
            for sg_id in all_5_sgs:
                if sg_id not in enabled_sgs:
                    tds.append('<td class="heat-missing missing" data-status="missing">待全量</td>')
                    continue
                c = (r.get("cells") or {}).get(sg_id) or {"count": 0, "revenue_6m_usd": 0, "exemplars": [], "heat_level": "absent"}
                cnt = int(c.get("count", 0))
                rev_m = float(c.get("revenue_6m_usd", 0)) / 1_000_000
                ex_links = "、".join(
                    f'<a href="#prod-{esc(prod_by_uid[str(ex["unified_app_id"])]["entity_id"])}">{esc(ex["app_name"][:14])}</a>'
                    if str(ex.get("unified_app_id")) in prod_by_uid and ("top10_revenue" in prod_by_uid[str(ex["unified_app_id"])]["roles"] or "download_supplement" in prod_by_uid[str(ex["unified_app_id"])]["roles"] or "whitelist_exception" in prod_by_uid[str(ex["unified_app_id"])]["roles"])
                    else esc(ex.get("app_name", "")[:14])
                    for ex in (c.get("exemplars") or [])[:2]
                )
                cell_body = "缺席" if cnt == 0 else f"<strong>{cnt}款</strong> · ${rev_m:.1f}M<br><small>{ex_links}</small>"
                tds.append(f'<td class="heat-{esc(c.get("heat_level","absent"))}">{cell_body}</td>')
            if with_signal:
                tds.append(f'<td><span class="signal-badge sig-{esc(r.get("signal_code","niche_watch"))}">{esc(r.get("signal_label","小众观察"))}</span></td>')
            out.append("<tr>" + "".join(tds) + "</tr>")
        return "".join(out)

    pair_rows = "".join(
        f'<tr><td>{_render_tag_pill(pr["theme_tag_id"], pr["theme_label"], "theme_primary")}</td>'
        f'<td>{_render_tag_pill(pr["presentation_tag_id"], pr["presentation_label"], "presentation")}</td>'
        f'<td class="num">{pr["count"]}</td>'
        f'<td>{esc("、".join(x["app_name"] for x in pr.get("products", [])[:4]))}</td></tr>'
        for pr in (matrix.get("pairs") or [])
    )

    opp_detail_cards = []
    for c in opps["cards"]:
        pills = "".join(_render_tag_pill(tid, vocab_map[tid]["label_zh"], vocab_map[tid]["axis"]) for tid in c["target_tag_ids"])
        mref_html = []
        for mr in (c["supply_gap_metrics"].get("metric_refs") or []):
            tok = metric_token(mr["json_pointer"], mr.get("match", {}), digits=mr.get("digits", 2),
                               divisor=mr.get("divisor", 1), suffix=mr.get("suffix", ""))
            mref_html.append(f'<li>{esc(mr.get("label","参考指标"))}：{tok}</li>')
        nr = c["new_release_validation"]
        fit_li = "".join(f'<li>{esc(x)}</li>' for x in c["merge2_fit_conditions"])
        cost_li = "".join(f'<li>{esc(x)}</li>' for x in c["counterexamples_and_costs"])
        act_li = "".join(f'<li>{esc(x)}</li>' for x in c["min_validation_actions"])
        film_links = " ".join(f'<a class="film-jump" href="#film-{esc(fid)}">影视借鉴：{esc(fid)}</a>' for fid in c["linked_film_ids"])
        opp_detail_cards.append(
            f'<article id="opp-{esc(c["opp_id"])}" class="opp-detail-card">'
            f'<div class="opp-detail-head"><span class="badge badge-opp">{esc(c["opportunity_type"])}</span><h3>{esc(c["title"])}</h3></div>'
            f'<div class="pill-row">{pills}</div>'
            f'<p>{esc(c["supply_gap_metrics"].get("benchmark_summary",""))}</p>'
            + (f'<ul class="metric-ref-list">{"".join(mref_html)}</ul>' if mref_html else "")
            + f'<p class="nr-val"><strong>新品验证状态</strong>：{esc(nr.get("note",""))}</p>'
            f'<details class="card-details" open><summary>二合适配条件、反例代价与最小验证动作</summary>'
            f'<div class="opp-cols"><div><strong>二合适配条件</strong><ul>{fit_li}</ul></div>'
            f'<div><strong>反例与代价</strong><ul>{cost_li}</ul></div></div>'
            f'<div><strong>立项最小验证动作</strong><ol>{act_li}</ol></div>'
            f'<div class="film-link-row">{film_links}</div></details></article>'
        )

    matrix_note = "（试点模式展示二合内部供给密度，其余四品类列标为待全量扩充）" if preset == "pilot" else ""
    signal_th = '<th>二合供给差信号</th>' if opportunities_enabled else ""
    matrix_cols = 7 if opportunities_enabled else 6
    theme_groups = defaultdict(list)
    for row in matrix.get("theme_rows", []):
        theme_groups[vocab_map[row["tag_id"]].get("parent_domain")].append(row)

    theme_parts = []
    for parent_domain, rows in theme_groups.items():
        if parent_domain in parent_labels:
            theme_parts.append(
                f'<tr class="group-divider"><td colspan="{matrix_cols}">{esc(parent_labels[parent_domain])}</td></tr>'
            )
        theme_parts.append(_render_matrix_rows(rows, opportunities_enabled))

    opp_block = "".join(opp_detail_cards) if opportunities_enabled else _opportunity_placeholder(preset_cfg, bool(open_l2))
    sec4_html = f"""<section id="sec-matrix" class="dash-section">
  <h2>4. Tag 供给差热力矩阵与立项机会详卡</h2>
  <p class="muted">按主题材与呈现两段展示各品类覆盖度与 6 个月 ST 净收入合计{esc(matrix_note)}。</p>
  <div class="table-wrap">
    <table class="data-table matrix-table">
      <thead><tr><th>受控 Tag</th><th>二合</th><th>三合</th><th>三消</th><th>模拟经营</th><th>混变益智</th>{signal_th}</tr></thead>
      <tbody>
        <tr class="group-divider"><td colspan="{matrix_cols}">一、主题材轴（Theme Primary）</td></tr>
        {''.join(theme_parts)}
        <tr class="group-divider"><td colspan="{matrix_cols}">二、呈现轴（Presentation）</td></tr>
        {_render_matrix_rows(matrix.get('presentation_rows', []), opportunities_enabled)}
      </tbody>
    </table>
  </div>
  <details class="info-fold"><summary>查看「主题材 × 呈现」二元组合频次对照表（{len(matrix.get('pairs') or [])} 组）</summary>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>主题材</th><th>呈现形式</th><th>命中款数</th><th>代表产品</th></tr></thead>
    <tbody>{pair_rows}</tbody></table></div>
  </details>
  <div class="opp-detail-grid">{opp_block}</div>
</section>"""

    # Section 5: Film Narrative References
    opp_title_map = {c["opp_id"]: c["title"] for c in opps["cards"]}
    film_cards = []
    for f in films["films"]:
        if preset == "pilot" and f.get("anchor_type") == "tag_fact":
            tag_id = _film_tag_id(f)
            anchor = f"tag-{tag_id}"
            anchor_label = vocab_map[tag_id]["label_zh"]
        else:
            oid = f["linked_opportunity_id"]
            anchor = f"opp-{oid}"
            anchor_label = opp_title_map[oid]
        cites = " ".join(f'[[cite {sid}]]' for sid in f["evidence_source_ids"])
        p1 = f["plot_paragraphs"][0]
        all_paras = "".join(f'<p>{esc(para)}</p>' for para in f["plot_paragraphs"])
        film_cards.append(
            f'<article id="film-{esc(f["film_id"])}" class="film-card">'
            f'<div class="film-head"><h3>{esc(f["title_zh"])} <small>{esc(f.get("title_orig",""))}</small></h3>'
            f'<a class="badge badge-opp" href="#{esc(anchor)}">挂钩：{esc(anchor_label)}</a></div>'
            f'<p class="film-hook"><strong>可借用叙事结构</strong>：{esc(f["narrative_hook"])}</p>'
            f'<p class="film-preview">{esc(p1[:110])}…</p>'
            f'<details class="card-details"><summary>展开完整剧情介绍（共 {len(f["plot_paragraphs"])} 段）</summary>'
            f'{all_paras}<div class="film-cite">剧情来源：{cites}</div></details></article>'
        )
    film_anchor_scope = "受控 Tag" if preset == "pilot" else "机会卡"
    sec5_html = f"""<section id="sec-films" class="dash-section">
  <h2>5. 影视剧情借鉴卡片墙</h2>
  <p class="muted">按{film_anchor_scope}挂钩选片（欧美高知名度剧影优先），每部提供 2–3 段完整剧情介绍与长来源追溯。</p>
  <div class="film-grid">{''.join(film_cards)}</div>
</section>"""

    # Section 6: Controlled Vocabulary, Blind QC & Statistical Scope Appendix
    tag_to_prods = defaultdict(list)
    for tp in corpus["products"]:
        uid = str(tp["unified_app_id"])
        rp = prod_by_uid.get(uid)
        if not rp:
            continue
        for t in tp["tags"]:
            tag_to_prods[t["tag_id"]].append(rp)

    vocab_cards = []
    for v in corpus["vocab"]:
        tid = v["tag_id"]
        inc = "；".join(v["include_examples"]) if isinstance(v["include_examples"], list) else str(v["include_examples"])
        exc = "；".join(v["exclude_examples"]) if isinstance(v["exclude_examples"], list) else str(v["exclude_examples"])
        backlinks = "、".join(
            f'<a href="#prod-{esc(p["entity_id"])}">{esc(p["app_name"])}</a>'
            if ("top10_revenue" in p["roles"] or "download_supplement" in p["roles"] or "whitelist_exception" in p["roles"])
            else f'<a href="#new-{esc(p["entity_id"])}">{esc(p["app_name"])}</a>'
            for p in tag_to_prods.get(tid, [])
        ) or "暂无命中"
        vocab_cards.append(
            f'<article id="tag-{esc(tid)}" class="vocab-card">'
            f'<div class="vocab-head"><span class="tag-pill axis-{esc(v["axis"])}">{esc(v["label_zh"])}</span>'
            f'<code>{esc(tid)}</code><small>{esc(v["label_en"])}</small></div>'
            f'<p>{esc(v["definition"])}</p>'
            f'<div class="vocab-rules"><span><strong>纳入例</strong>：{esc(inc)}</span> · <span><strong>排除例</strong>：{esc(exc)}</span></div>'
            f'<div class="vocab-links"><strong>命中产品</strong>：{backlinks}</div></article>'
        )

    qc_sum = corpus["qc_summary"]
    qc_rows = "".join(
        f'<tr><td>{esc(c["app_name"])}</td><td>{esc(c["primary_theme"])}</td><td>{esc(c["blind_theme"])}</td>'
        f'<td>{("一致" if c["theme_match"] else "分歧")}</td><td class="num">{c["set_overlap"]*100:.1f}%</td></tr>'
        for c in (qc_sum.get("comparisons") or [])
    )
    adj_rows = "".join(
        f'<li><strong>{esc(a["app_name"])}</strong>：{esc(a["note"])}</li>'
        for a in (corpus.get("adjudications") or [])
    )
    gap_table = '<p class="muted">无登记缺口</p>'
    if gaps:
        gap_headers = "".join(f"<th>{label}</th>" for _, label in GAP_COLUMNS)
        gap_rows = "".join(
            "<tr>" + "".join(f"<td>{_gap_cell(gap.get(field))}</td>" for field, _ in GAP_COLUMNS) + "</tr>"
            for gap in gaps
        )
        gap_table = (f'<div class="table-wrap"><table class="data-table"><thead><tr>{gap_headers}</tr></thead>'
                     f'<tbody>{gap_rows}</tbody></table></div>')

    sec6_html = f"""<section id="sec-appendix" class="dash-section">
  <h2>6. 受控词表与数据口径附录</h2>
  <h3>6.1 受控 Tag 词表索引（共 {len(corpus['vocab'])} 词）</h3>
  <div class="vocab-grid">{''.join(vocab_cards)}</div>
  <h3>6.2 一致性重测读数与人工终裁</h3>
  <p class="scope-pill">{esc(qc_sum['protocol_disclosure'])}</p>
  <p>抽检样本：{qc_sum['sample_evaluated']} 款 · 槽位 1 主题材一致率：<strong>{qc_sum['theme_primary_agreement']*100:.1f}%</strong>（门槛 ≥80%）· 全标签集合重叠率：<strong>{qc_sum['mean_tag_set_overlap']*100:.1f}%</strong>（门槛 ≥75%）。</p>
  <details class="info-fold"><summary>展开抽检逐款明细与终裁记录</summary>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>产品</th><th>主定标主题材</th><th>盲测主题材</th><th>判定</th><th>集合重叠率</th></tr></thead>
    <tbody>{qc_rows}</tbody></table></div>
    {f'<ul>{adj_rows}</ul>' if adj_rows else '<p class="muted">本次无额外分歧终裁记录。</p>'}
  </details>
  <h3>6.3 统计口径与估算边界声明</h3>
  <div class="scope-box">
    <ul>
      <li><strong>收入与下载来源</strong>：全部市场大盘、榜单、近似 RPD 与环比均由 Sensor Tower 双端合并估算算出（<code>revenue_basis: iap_net</code>），为扣除平台分成后的应用内购净收入估算，不含广告变现（IAA）及站外网页充值；若与未扣渠道分成的毛流水口径横向对照，通常存在约三成渠道分成差异。</li>
      <li><strong>地理与商店覆盖</strong>：采用 Sensor Tower 全球口径（App Store + Google Play，包含中国大陆 iOS，不含中国大陆第三方安卓商店与小游戏）。纯中国大陆 iOS 发行产品已在产品卡展开层单独注记。</li>
      <li><strong>隔离与去重原则</strong>：模拟经营与混变益智的下载增补位、以及混变益智中个别 3D 配对单列特例均不计入自动标签池 CR10 分母；混变益智与前四类玩法子类正交，不可相加求和大盘。</li>
    </ul>
  </div>
  <h3>6.4 脱敏数据来源卡</h3>
  <section id="sources" class="sources-grid">[[sources]]</section>
  <h3>6.5 缺项登记表</h3>
  {gap_table}
</section>"""

    inline_script_tag = (
        f'\n<script data-approved-inline="dashboard-ui">\n{APPROVED_INLINE_JS}\n</script>'
        if script_mode == "whitelisted_inline_js" else ""
    )

    template_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>[[style]]</style>
</head>
<body class="mode-{esc(preset)}">
  <div class="dash-layout">
    {sidebar_html}
    <main class="dash-main">
      {sec0_html}
      {sec1_html}
      {sec2_html}
      {sec3_html}
      {sec4_html}
      {sec5_html}
      {sec6_html}
    </main>
  </div>{inline_script_tag}
</body>
</html>
"""

    # Pre-render anchor closure & forbidden pattern audit
    checker = AnchorChecker(template_html)
    if checker.dup_ids:
        raise ValueError(f"duplicate HTML ids generated: {sorted(set(checker.dup_ids))}")
    missing_anchors = [a for a in set(checker.hrefs) if a not in checker.ids and not a.startswith("src-")]
    if missing_anchors:
        raise ValueError(f"unresolved internal anchors in compiled template: {sorted(missing_anchors)}")

    forbid_hits = scan_forbidden_patterns(template_html, workspace_cfg["sanitization_policy"]["forbidden_public_patterns"])
    if forbid_hits:
        raise ValueError(f"desensitization check failed on template: {forbid_hits[:5]}")

    tpl_path = out_dir / "research/report.template.html"
    tpl_path.write_text(template_html, encoding="utf-8")

    delivery_input = {
        "template": "report.template.html",
        "script_mode": script_mode,
        "results": {"market": "data/market_result.json"},
        "sources": merged_sources,
        "assets": assets_manifest,
        "charts": charts_manifest,
    }
    di_path = out_dir / "research/delivery-input.json"
    write(di_path, delivery_input)

    print(json.dumps({
        "status": "compiled",
        "preset": preset,
        "script_mode": script_mode,
        "template": str(tpl_path),
        "delivery_input": str(di_path),
        "charts": len(charts_manifest),
        "icons": len(assets_manifest),
        "sources": len(merged_sources),
        "desensitization_hits": 0,
    }, ensure_ascii=False))
    return 0


def cmd_pack_bundle(args) -> int:
    workspace_cfg, _, _ = load_configs()
    out_dir = Path(args.out_dir).resolve()
    deliv_meta = read(out_dir / "research/delivery.json")
    deliverables = [Path(p) for p in deliv_meta.get("deliverables", [])]
    ext_html = next((p for p in deliverables if p.name.endswith(".html") and not p.name.endswith(".base64.html")), None)
    b64_html = next((p for p in deliverables if p.name.endswith(".base64.html")), None)
    if not ext_html or not b64_html or not ext_html.is_file() or not b64_html.is_file():
        raise ValueError("rendered HTML pair not found in research/delivery.json")

    patterns = workspace_cfg["sanitization_policy"]["forbidden_public_patterns"]
    all_hits = []
    for hp in (ext_html, b64_html):
        hits = scan_forbidden_patterns(hp.read_text(encoding="utf-8"), patterns)
        for h in hits:
            all_hits.append({"file": hp.name, **h})
    if all_hits:
        raise ValueError("post-render desensitization scan failed: " + json.dumps(all_hits[:5], ensure_ascii=False))

    max_mb = float(workspace_cfg["delivery_policy"].get("standalone_max_mb", 3.0))
    b64_mb = b64_html.stat().st_size / (1024 * 1024)
    if b64_mb > max_mb:
        raise ValueError(f"standalone HTML {b64_mb:.2f} MB exceeds limit {max_mb:.2f} MB")

    zip_name = ext_html.stem + workspace_cfg["delivery_policy"]["html_pair_naming"]["bundle_zip_suffix"]
    zip_path = out_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(ext_html, arcname=ext_html.name)
        img_dir = out_dir / "image"
        if img_dir.is_dir():
            for item in sorted(img_dir.rglob("*")):
                if item.is_file():
                    zf.write(item, arcname=item.relative_to(out_dir).as_posix())

    print(json.dumps({
        "status": "packed",
        "external_html": str(ext_html),
        "standalone_html": str(b64_html),
        "standalone_mb": round(b64_mb, 3),
        "bundle_zip": str(zip_path),
        "desensitization_hits": 0,
        "shortlink_notice": workspace_cfg["delivery_policy"]["shortlink_delivery_notice"],
    }, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 4 dashboard compiler and bundle packager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_b = sub.add_parser("build", help="Compile Stage 1-3 JSONs into research/report.template.html and delivery-input.json")
    p_b.add_argument("--out-dir", required=True)
    p_b.add_argument("--preset", choices=("pilot", "full"), default=None)
    p_b.add_argument("--script-mode", choices=("zero_js", "whitelisted_inline_js"), default=None)
    p_b.set_defaults(func=cmd_build)

    p_p = sub.add_parser("pack-bundle", help="Run final HTML desensitization scan, size check and build _bundle.zip")
    p_p.add_argument("--out-dir", required=True)
    p_p.set_defaults(func=cmd_pack_bundle)
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

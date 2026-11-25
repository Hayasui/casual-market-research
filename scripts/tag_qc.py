"""Stage 2 Tag Quality Control: vocab gate, slot/evidence gate, blind sampling, consistency & finalization.

Pure Python standard library. Reads rules exclusively from configs/tag_blacklist.json.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import read, write


NARRATIVE_AXIS = "narrative_motif"
INTENSITY_AXIS = "narrative_intensity"
OFFICIAL_MORPHEMES = ("复建", "复耕", "选装", "物业", "镇区", "影都", "复苏", "矩阵", "体系")
COMPOUND_LABEL_RE = re.compile(r"[/／、()（）\[\]［］【】或]")


def _main_tags(product):
    return [tag for tag in product.get("tags") or [] if tag.get("slot") != NARRATIVE_AXIS]


def _narrative_values(product):
    values = []
    if product.get(NARRATIVE_AXIS) not in (None, ""):
        values.append(product[NARRATIVE_AXIS])
    values.extend(
        tag.get("value") or tag.get("label_zh") or tag.get("tag_id")
        for tag in product.get("tags") or [] if tag.get("slot") == NARRATIVE_AXIS
    )
    return values


def _checked_narrative(product, allowed):
    values = _narrative_values(product)
    if len(values) > 1 or any(not isinstance(v, str) or v not in allowed for v in values):
        raise ValueError(f"{product.get('unified_app_id')}: invalid narrative_motif {values!r}; expected at most one controlled value")
    return next(iter(values), None)


def _checked_narrative_intensity(prod, cfg):
    uid = prod.get("unified_app_id")
    intensity = prod.get(INTENSITY_AXIS)
    allowed = cfg[INTENSITY_AXIS]["controlled_enum"]
    if intensity not in allowed:
        raise ValueError(f"{uid}: invalid narrative_intensity {intensity!r}; expected one of {allowed}")
    motif = _checked_narrative(prod, cfg[NARRATIVE_AXIS]["controlled_enum"])
    linkage = cfg[INTENSITY_AXIS]["motif_linkage"][intensity]
    if linkage == "must_be_null" and motif is not None:
        raise ValueError(f"{uid}: narrative_motif must be empty for {intensity}")
    if linkage == "must_be_non_null" and motif is None:
        raise ValueError(f"{uid}: narrative_motif is required for {intensity}")
    return intensity


def _narrative_evidence_errors(prod, intensity, sources, cfg):
    pname = prod.get("app_name") or prod["unified_app_id"]
    evidence = prod.get("narrative_evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        return [f"{pname}: narrative_evidence must be an array of objects"]

    policy = cfg[INTENSITY_AXIS]["evidence_policy"]
    high_types = set(policy["high_reliability_types"])
    non_mkt_types = set(cfg["evidence_rules"]["non_marketing_evidence_types"])
    errors = []
    distinct = set()
    for idx, item in enumerate(evidence):
        label = f"{pname}/narrative_evidence[{idx}]"
        ev_type = item.get("evidence_type")
        ev_src = item.get("evidence_source_id")
        ev_quote = item.get("evidence_quote")
        marketing_only = item.get("marketing_only")
        if marketing_only is True or ev_type == "ad_creative":
            errors.append(f"{label}: marketing-only evidence forbidden for narrative_evidence")
        elif marketing_only is not False:
            errors.append(f"{label}: marketing_only must be false")
        elif not isinstance(ev_type, str) or ev_type not in non_mkt_types:
            errors.append(f"{label}: invalid evidence_type {ev_type!r}")
        elif not isinstance(ev_src, str) or not ev_src or ev_src not in sources:
            errors.append(f"{label}: unknown evidence_source_id {ev_src!r} in bundle sources")
        elif not isinstance(ev_quote, str) or not ev_quote.strip():
            errors.append(f"{label}: missing evidence_quote")
        else:
            distinct.add((ev_type, ev_src))

    if intensity == "无剧情":
        if len(distinct) < policy["min_evidence_for_none"]:
            errors.append(f"{pname}: 无剧情 requires at least one non-marketing verification record")
    elif prod.get("confidence") != "low":
        high_count = sum(ev_type in high_types for ev_type, _ in distinct)
        if len(distinct) < policy["min_evidence_for_story"] or high_count < policy["min_high_reliability_for_story"]:
            errors.append(f"{pname}: {intensity} requires at least two distinct non-marketing records, including one high-reliability source")
    return errors


def load_blacklist_cfg() -> dict:
    return read(SKILL_ROOT / "configs/tag_blacklist.json")


def normalize_term(value: str) -> str:
    return re.sub(r"[\s\-_.,!?:;/·•()（）【】\[\]\"'’]+", "", str(value or "").strip().lower())


def build_banned_set(cfg: dict) -> set[str]:
    banned = set()
    terms = cfg.get("banned_generic_terms", {})
    for group_key, group_val in terms.items():
        if group_key == "matching_policy" or not isinstance(group_val, dict):
            continue
        for lang_list in group_val.values():
            if isinstance(lang_list, list):
                for item in lang_list:
                    banned.add(normalize_term(item))
    return banned


def _extract_vocab_entries(vocab_obj) -> list[dict]:
    if isinstance(vocab_obj, list):
        return vocab_obj
    if isinstance(vocab_obj, dict):
        for key in ("tags", "vocab", "entries"):
            if isinstance(vocab_obj.get(key), list):
                return vocab_obj[key]
    raise ValueError("vocab JSON must be a list or contain a 'tags'/'vocab' array")


def _extract_product_list(obj) -> list[dict]:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("products"), list):
        return obj["products"]
    raise ValueError("JSON must be a list or contain a 'products' array")


def cmd_validate_vocab(args) -> int:
    cfg = load_blacklist_cfg()
    mode_cfg = cfg["mode_thresholds"][args.preset]
    fmt = cfg["tag_format"]
    axes = set(cfg["slots"]["axes"])
    primary_domain_ids = {
        domain["id"] for domain in cfg["primary_domains"]
        if args.preset == "full" or not domain.get("full_only")
    }
    axis_forbidden = cfg.get("axis_forbidden_terms", {})
    morphemes = cfg.get("official_morphemes", OFFICIAL_MORPHEMES)
    banned = build_banned_set(cfg)
    id_re = re.compile(fmt["tag_id_regex"])

    vocab_raw = read(Path(args.vocab).resolve())
    entries = _extract_vocab_entries(vocab_raw)
    errors, warnings = [], []

    main_count = sum(item.get("axis") in axes for item in entries)
    if not (mode_cfg["vocab_min"] <= main_count <= mode_cfg["vocab_max"]):
        errors.append(
            f"vocab size {main_count} outside {args.preset} range [{mode_cfg['vocab_min']}, {mode_cfg['vocab_max']}]"
        )

    seen_ids, seen_zh, seen_en = set(), set(), set()
    entry_map = {}
    hits_by_axis = defaultdict(set)
    hits_by_id = {}
    for idx, item in enumerate(entries):
        tid = str(item.get("tag_id") or "")
        zh = str(item.get("label_zh") or "").strip()
        en = str(item.get("label_en") or "").strip()
        axis = str(item.get("axis") or "")
        if not id_re.fullmatch(tid):
            errors.append(f"[{idx}] invalid tag_id '{tid}' (must match {fmt['tag_id_regex']})")
        if tid in seen_ids:
            errors.append(f"duplicate tag_id: {tid}")
        seen_ids.add(tid)
        entry_map[tid] = item

        if not zh:
            errors.append(f"{tid}: missing label_zh")
        else:
            if len(zh) > int(fmt["label_zh_hard_max_chars"]):
                errors.append(f"{tid}: label_zh '{zh}' exceeds hard max {fmt['label_zh_hard_max_chars']} chars")
            elif len(zh) > int(fmt["label_zh_recommended_max_chars"]):
                warnings.append(f"{tid}: label_zh '{zh}' exceeds recommended {fmt['label_zh_recommended_max_chars']} chars")
            zh_norm = normalize_term(zh)
            if zh_norm in seen_zh:
                errors.append(f"{tid}: duplicate normalized label_zh '{zh}'")
            seen_zh.add(zh_norm)

        if fmt.get("require_label_en", True) and not en:
            errors.append(f"{tid}: missing label_en")
        if en:
            en_norm = normalize_term(en)
            if en_norm in seen_en:
                errors.append(f"{tid}: duplicate normalized label_en '{en}'")
            seen_en.add(en_norm)
        if axis not in axes | {NARRATIVE_AXIS}:
            errors.append(f"{tid}: invalid axis '{axis}' (expected one of {sorted(axes | {NARRATIVE_AXIS})})")
        if axis == "theme_primary":
            parent_domain = item.get("parent_domain")
            if parent_domain in (None, ""):
                errors.append(f"{tid}: missing parent_domain")
            elif not isinstance(parent_domain, str) or parent_domain not in primary_domain_ids:
                errors.append(f"{tid}: invalid parent_domain {parent_domain!r} for {args.preset}")
        if axis == NARRATIVE_AXIS and zh not in cfg[NARRATIVE_AXIS]["controlled_enum"]:
            errors.append(f"{tid}: invalid narrative_motif '{zh}'")
        hit_ids = item.get("hit_app_ids", [])
        if not isinstance(hit_ids, list):
            errors.append(f"{tid}: hit_app_ids must be an array")
        else:
            hits = {str(uid).strip() for uid in hit_ids if uid is not None and str(uid).strip()}
            hits_by_id[tid] = hits
            hits_by_axis[axis].update(hits)
            if axis in axes and not hits:
                errors.append(f"{tid}: hit_app_ids must contain at least one app ID")
            if len(hits) == 1 and item.get("emerging") is not True:
                warnings.append(f"{tid}: one hit without emerging=true")
        if fmt.get("require_definition", True) and not str(item.get("definition") or "").strip():
            errors.append(f"{tid}: missing definition")
        if fmt.get("require_include_and_exclude_examples", True):
            inc = item.get("include_examples")
            exc = item.get("exclude_examples")
            if not inc or (isinstance(inc, list) and not any(str(x).strip() for x in inc)):
                errors.append(f"{tid}: missing include_examples")
            if not exc or (isinstance(exc, list) and not any(str(x).strip() for x in exc)):
                errors.append(f"{tid}: missing exclude_examples")

        for field_name, val in (("tag_id", tid), ("label_zh", zh), ("label_en", en)):
            normalized = normalize_term(val)
            if normalized in banned:
                errors.append(f"{tid}: banned generic term hit in {field_name}='{val}'")
            if COMPOUND_LABEL_RE.search(val):
                errors.append(f"{tid}: compound label in {field_name}='{val}'")
            for term in axis_forbidden.get(axis, []):
                if normalize_term(term) in normalized:
                    errors.append(f"{tid}: axis_forbidden_terms hit '{term}' in {field_name}='{val}'")
        matched = [term for term in morphemes if normalize_term(term) in normalize_term(zh)]
        if matched:
            warnings.append(f"{tid}: official morphemes in label_zh: {'、'.join(matched)}")

    for tid, item in entry_map.items():
        axis = item.get("axis")
        hits = len(hits_by_id.get(tid, set()))
        axis_total = len(hits_by_axis[axis])
        if axis in axes and axis_total and hits * 5 > axis_total * 2:
            warnings.append(f"{tid}: {axis} hit share {hits}/{axis_total} ({hits / axis_total:.1%}) exceeds 40%")

    # Freeze existing pilot tag_ids when expanding to full
    if args.prior_vocab and mode_cfg.get("freeze_existing_pilot_tag_ids_on_expand", False):
        prior_entries = _extract_vocab_entries(read(Path(args.prior_vocab).resolve()))
        for pitem in prior_entries:
            ptid = pitem.get("tag_id")
            if ptid not in entry_map:
                errors.append(f"frozen prior tag_id missing on expand: {ptid}")
            elif entry_map[ptid].get("axis") != pitem.get("axis"):
                errors.append(f"frozen prior tag_id axis changed: {ptid}")

    result = {
        "status": "passed" if not errors else "failed",
        "preset": args.preset,
        "vocab_count": main_count,
        "hit_counts": {tid: len(hits) for tid, hits in hits_by_id.items()},
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def cmd_validate_tags(args) -> int:
    cfg = load_blacklist_cfg()
    slots_cfg = cfg["slots"]
    ev_cfg = cfg["evidence_rules"]
    banned = build_banned_set(cfg)

    vocab_entries = _extract_vocab_entries(read(Path(args.vocab).resolve()))
    vocab_map = {e["tag_id"]: e for e in vocab_entries}
    tags_products = _extract_product_list(read(Path(args.tags).resolve()))
    roster_products = _extract_product_list(read(Path(args.roster).resolve()))
    roster_map = {str(p["unified_app_id"]): p for p in roster_products}
    bundle = read(Path(args.bundle).resolve())
    sources = bundle.get("sources") if isinstance(bundle, dict) else None
    if not isinstance(sources, dict):
        raise ValueError("tagging input bundle must contain a sources object")

    errors, warnings, low_conf_ids = [], [], []
    tagged_uids = set()
    non_mkt_types = set(ev_cfg["non_marketing_evidence_types"])
    valid_ev_types = set(ev_cfg["valid_evidence_types"])
    valid_conf = set(ev_cfg["valid_confidence_levels"])
    min_tags = int(slots_cfg["pass2_final_tags_per_product"]["min"])
    max_tags = int(slots_cfg["pass2_final_tags_per_product"]["max"])

    for prod in tags_products:
        uid = str(prod.get("unified_app_id") or "")
        pname = prod.get("app_name") or uid
        if not uid:
            errors.append("product entry missing unified_app_id")
            continue
        if uid in tagged_uids:
            errors.append(f"duplicate tagged product: {uid} ({pname})")
        tagged_uids.add(uid)
        if uid not in roster_map:
            errors.append(f"tagged product not in roster: {uid} ({pname})")

        raw_tags = prod.get("tags")
        if not isinstance(raw_tags, list) or any(not isinstance(tag, dict) for tag in raw_tags):
            errors.append(f"{pname}: tags must be an array of objects")
            continue
        try:
            intensity = _checked_narrative_intensity(prod, cfg)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            errors.extend(_narrative_evidence_errors(prod, intensity, sources, cfg))

        shots_rev = prod.get("screenshots_reviewed")
        if shots_rev is None:
            shots_rev = []
        if not isinstance(shots_rev, list):
            errors.append(f"{pname}: screenshots_reviewed must be an array")
            shots_rev = []
        available_shots = (roster_map.get(uid) or {}).get("screenshot_files") or []
        for shot in shots_rev:
            if not isinstance(shot, str) or shot not in available_shots:
                errors.append(f"{pname}: reviewed screenshot {shot!r} not in roster screenshot_files")
        if ev_cfg.get("presentation_slot_requires_visual_check", True) and available_shots and not shots_rev:
            errors.append(f"{pname}: screenshots_reviewed is empty while {len(available_shots)} local screenshots exist")

        for tag in raw_tags:
            tid = tag.get("tag_id") or tag.get("slot") or ""
            ev_src = str(tag.get("evidence_source_id") or "").strip()
            if not ev_src:
                errors.append(f"{pname}/{tid}: missing evidence_source_id")
            elif ev_src not in sources:
                errors.append(f"{pname}/{tid}: unknown evidence_source_id {ev_src!r} in bundle sources")
            elif tag.get("evidence_type") == "store_screenshot":
                source = sources[ev_src]
                shot = source.get("raw_file") if isinstance(source, dict) else None
                if not shot or shot not in shots_rev:
                    errors.append(f"{pname}/{tid}: source {ev_src!r} screenshot {shot!r} not in screenshots_reviewed")

        tags = _main_tags(prod)
        if not min_tags <= len(tags) <= max_tags:
            errors.append(f"{pname}: tag count {len(tags)} outside [{min_tags}, {max_tags}]")
            continue

        slot_counts = defaultdict(int)
        seen_tids = set()
        for t in tags:
            slot = str(t.get("slot") or "")
            tid = str(t.get("tag_id") or "")
            zh = str(t.get("label_zh") or "")
            slot_counts[slot] += 1
            if slot not in slots_cfg["axes"]:
                errors.append(f"{pname}: unknown main slot '{slot}'")
            if tid in seen_tids:
                errors.append(f"{pname}: duplicate tag_id '{tid}'")
            seen_tids.add(tid)
            if tid not in vocab_map:
                errors.append(f"{pname}: unknown tag_id '{tid}' (not in approved vocab)")
            elif vocab_map[tid].get("axis") != slot:
                errors.append(f"{pname}: tag '{tid}' slot '{slot}' mismatches vocab axis '{vocab_map[tid].get('axis')}'")
            if normalize_term(tid) in banned or normalize_term(zh) in banned:
                errors.append(f"{pname}: banned generic term in tag '{tid}'/'{zh}'")

            ev_type = str(t.get("evidence_type") or "")
            mkt_only = bool(t.get("marketing_only", False))
            ev_quote = str(t.get("evidence_quote") or "").strip()
            if ev_type not in valid_ev_types:
                errors.append(f"{pname}/{tid}: invalid evidence_type '{ev_type}'")
            if ev_cfg.get("forbid_marketing_only_final_tag", True):
                if mkt_only or ev_type not in non_mkt_types:
                    errors.append(f"{pname}/{tid}: marketing-only evidence forbidden for final tag")
            if not ev_quote:
                errors.append(f"{pname}/{tid}: missing evidence_quote")
            elif len(ev_quote.split()) > int(ev_cfg.get("max_evidence_quote_words", 40)):
                warnings.append(f"{pname}/{tid}: evidence_quote exceeds {ev_cfg['max_evidence_quote_words']} words")

        for axis_name, axis_rule in slots_cfg["axes"].items():
            cnt = slot_counts[axis_name]
            if cnt < int(axis_rule["min"]) or cnt > int(axis_rule["max"]):
                errors.append(f"{pname}: slot '{axis_name}' count {cnt} outside [{axis_rule['min']}, {axis_rule['max']}]")

        if ev_cfg.get("require_excluded_near_tags", True):
            excl = prod.get("excluded_near_tags")
            if not isinstance(excl, list) or not any(str(x).strip() for x in excl):
                errors.append(f"{pname}: missing excluded_near_tags")

        conf = str(prod.get("confidence") or "")
        if conf not in valid_conf:
            errors.append(f"{pname}: invalid confidence '{conf}'")
        elif conf == "low":
            low_conf_ids.append(uid)

    missing_uids = sorted(set(roster_map.keys()) - tagged_uids)
    for muid in missing_uids:
        errors.append(f"roster product missing tags: {muid} ({roster_map[muid].get('app_name')})")

    result = {
        "status": "passed" if not errors else "failed",
        "preset": args.preset,
        "roster_count": len(roster_map),
        "tagged_count": len(tagged_uids),
        "low_confidence_ids": low_conf_ids,
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def _strip_blind_fields(prod_bundle: dict, strip_keys: set[str]) -> dict:
    cleaned = deepcopy(prod_bundle)
    for k in list(cleaned.keys()):
        if k in strip_keys:
            cleaned.pop(k, None)
    l1 = cleaned.get("layer1_store_facts")
    if isinstance(l1, dict):
        for k in list(l1.keys()):
            if k in strip_keys or "game_iq" in k.lower():
                l1.pop(k, None)
    return cleaned


def cmd_sample(args) -> int:
    cfg = load_blacklist_cfg()
    mode_cfg = cfg["mode_thresholds"][args.preset]
    proto = cfg["blind_retest_protocol"]
    strip_keys = set(proto["strip_input_fields"]) | {INTENSITY_AXIS, NARRATIVE_AXIS, "narrative_evidence"}

    bundle = read(Path(args.bundle).resolve())
    bundle_map = {str(p["unified_app_id"]): p for p in _extract_product_list(bundle)}
    tags_map = {str(p["unified_app_id"]): p for p in _extract_product_list(read(Path(args.tags).resolve()))}
    roster_list = _extract_product_list(read(Path(args.roster).resolve()))
    rng = random.Random(args.seed)

    by_sg = defaultdict(list)
    for p in roster_list:
        uid = str(p["unified_app_id"])
        if uid in bundle_map:
            by_sg[p.get("primary_subgenre", "unknown")].append(p)

    sampled_uids: list[str] = []

    def pick_stratified(candidates: list[dict], target_k: int):
        chosen = []
        chosen_set = set()

        def try_add(pool):
            for item in pool:
                if len(chosen) >= target_k:
                    break
                uid = str(item["unified_app_id"])
                if uid not in chosen_set:
                    chosen.append(uid)
                    chosen_set.add(uid)

        # 1. Top veteran (rank 1..3)
        veterans = sorted([c for c in candidates if c.get("subgenre_rank") is not None], key=lambda x: x["subgenre_rank"])
        try_add(veterans[:1])
        # 2. Low/medium confidence or multi-slot ambiguous
        ambiguous = [
            c for c in candidates
            if (tags_map.get(str(c["unified_app_id"]), {}).get("confidence") in {"low", "medium"})
            or len(_main_tags(tags_map.get(str(c["unified_app_id"]), {}))) == 3
        ]
        rng.shuffle(ambiguous)
        try_add(ambiguous[: max(1, target_k // 3)])
        # 3. New releases or download supplements
        emerging = [c for c in candidates if c.get("is_new_release") or "download_supplement" in (c.get("roles") or [])]
        rng.shuffle(emerging)
        try_add(emerging[: max(1, target_k - len(chosen))])
        # 4. Fill remaining deterministically
        rem = [c for c in candidates if str(c["unified_app_id"]) not in chosen_set]
        rng.shuffle(rem)
        try_add(rem)
        return chosen

    if args.preset == "full":
        per_sg = int(mode_cfg.get("blind_sample_per_subgenre", 3))
        for sg_id in sorted(by_sg.keys()):
            sampled_uids.extend(pick_stratified(by_sg[sg_id], per_sg))
        target_total = int(mode_cfg["blind_sample_size"])
        if len(sampled_uids) < target_total:
            rest = [p for p in roster_list if str(p["unified_app_id"]) not in set(sampled_uids) and str(p["unified_app_id"]) in bundle_map]
            sampled_uids.extend(pick_stratified(rest, target_total - len(sampled_uids)))
    else:
        target_total = int(mode_cfg["blind_sample_size"])
        all_cands = [p for p in roster_list if str(p["unified_app_id"]) in bundle_map]
        sampled_uids = pick_stratified(all_cands, target_total)

    blind_products = [_strip_blind_fields(bundle_map[uid], strip_keys) for uid in sampled_uids]
    output = {
        "schema_version": "1.0.0",
        "preset": args.preset,
        "protocol": "blind_falsification_first_remote_retest",
        "disclosure_label": proto["disclosure_label"],
        "falsification_instruction": proto["falsification_instruction"],
        "sample_size": len(blind_products),
        "products": blind_products,
    }
    out_path = Path(args.output).resolve()
    write(out_path, output)
    print(json.dumps({"status": "sampled", "output": str(out_path), "sample_size": len(blind_products),
                      "sampled_ids": sampled_uids}, ensure_ascii=False))
    return 0


def cmd_evaluate(args) -> int:
    cfg = load_blacklist_cfg()
    mode_cfg = cfg["mode_thresholds"][args.preset]
    proto = cfg["blind_retest_protocol"]

    tags_list = _extract_product_list(read(Path(args.tags).resolve()))
    blind_list = _extract_product_list(read(Path(args.blind).resolve()))
    primary_map = {str(p["unified_app_id"]): p for p in tags_list}

    required_n = int(mode_cfg["blind_sample_size"])
    errors = []
    if len(blind_list) < required_n:
        errors.append(f"blind sample count {len(blind_list)} < required {required_n} for preset '{args.preset}'")

    comparisons = []
    disagreements = []
    axes = tuple(cfg["slots"]["axes"])
    match_counts = dict.fromkeys((*axes, INTENSITY_AXIS, NARRATIVE_AXIS), 0)
    overlaps = []
    colliding_pairs = {axis: defaultdict(int) for axis in match_counts}
    allowed_narratives = cfg[NARRATIVE_AXIS]["controlled_enum"]

    for bprod in blind_list:
        uid = str(bprod.get("unified_app_id") or "")
        if uid not in primary_map:
            errors.append(f"blind product not found in primary tags_final: {uid}")
            continue
        if proto.get("require_falsification_first_prompt", True):
            fexcl = bprod.get("falsified_exclusions") or []
            if len(fexcl) < 2 or any(not str(x.get("counter_evidence") or "").strip() for x in fexcl if isinstance(x, dict)):
                errors.append(f"{uid}: blind output missing >=2 falsified_exclusions with counter_evidence")

        pprod = primary_map[uid]
        p_tags = [t for t in _main_tags(pprod) if t.get("slot") in axes and t.get("tag_id")]
        b_tags = [t for t in _main_tags(bprod) if t.get("slot") in axes and t.get("tag_id")]
        p_slots = {t["slot"]: t["tag_id"] for t in p_tags}
        b_slots = {t["slot"]: t["tag_id"] for t in b_tags}
        p_set = {t["tag_id"] for t in p_tags}
        b_set = {t["tag_id"] for t in b_tags}
        p_slots[NARRATIVE_AXIS] = _checked_narrative(pprod, allowed_narratives)
        b_slots[NARRATIVE_AXIS] = _checked_narrative(bprod, allowed_narratives)
        p_slots[INTENSITY_AXIS] = _checked_narrative_intensity(pprod, cfg)
        b_slots[INTENSITY_AXIS] = _checked_narrative_intensity(bprod, cfg)

        axis_matches = {}
        for axis in match_counts:
            first, second = p_slots.get(axis), b_slots.get(axis)
            required = cfg["slots"]["axes"].get(axis, {}).get("required", False)
            axis_matches[axis] = first == second and (first is not None or not required)
            if axis_matches[axis]:
                match_counts[axis] += 1
            elif first and second:
                colliding_pairs[axis][" <-> ".join(sorted((first, second)))] += 1

        denom = len(p_set | b_set)
        overlap = (len(p_set & b_set) / denom) if denom > 0 else 0.0
        overlaps.append(overlap)

        comp = {
            "unified_app_id": uid,
            "app_name": pprod.get("app_name") or bprod.get("app_name") or uid,
            "primary_theme": p_slots.get("theme_primary"),
            "blind_theme": b_slots.get("theme_primary"),
            "theme_match": axis_matches["theme_primary"],
            "primary_presentation": p_slots.get("presentation"),
            "blind_presentation": b_slots.get("presentation"),
            "presentation_match": axis_matches["presentation"],
            "primary_fusion": p_slots.get("fusion_secondary"),
            "blind_fusion": b_slots.get("fusion_secondary"),
            "fusion_match": axis_matches["fusion_secondary"],
            "primary_narrative_intensity": p_slots[INTENSITY_AXIS],
            "blind_narrative_intensity": b_slots[INTENSITY_AXIS],
            "narrative_intensity_match": axis_matches[INTENSITY_AXIS],
            "primary_narrative_motif": p_slots[NARRATIVE_AXIS],
            "blind_narrative_motif": b_slots[NARRATIVE_AXIS],
            "narrative_match": axis_matches[NARRATIVE_AXIS],
            "primary_tags": sorted(p_set),
            "blind_tags": sorted(b_set),
            "set_overlap": round(overlap, 4),
        }
        comparisons.append(comp)
        if not all(axis_matches.values()) or overlap < 1.0:
            disagreements.append(comp)

    n_eval = len(comparisons)
    agreement = {axis: count / n_eval if n_eval else 0.0 for axis, count in match_counts.items()}
    theme_rate = agreement["theme_primary"]
    mean_overlap = (sum(overlaps) / n_eval) if n_eval else 0.0
    theme_min = float(mode_cfg["theme_primary_agreement_min"])
    presentation_min = float(mode_cfg["presentation_agreement_min"])
    overlap_min = float(mode_cfg["tag_set_overlap_min"])
    passed_thresholds = (
        not errors and theme_rate >= theme_min and agreement["presentation"] >= presentation_min
        and mean_overlap >= overlap_min
    )

    low_conf = [
        {"unified_app_id": str(p["unified_app_id"]), "app_name": p.get("app_name"), "tags": p.get("tags")}
        for p in tags_list if p.get("confidence") == "low"
    ]

    report = {
        "schema_version": "1.0.0",
        "preset": args.preset,
        "protocol_disclosure": proto["disclosure_label"],
        "sample_evaluated": n_eval,
        "sample_required": required_n,
        "theme_primary_agreement": round(theme_rate, 4),
        "presentation_agreement": round(agreement["presentation"], 4),
        "fusion_agreement": round(agreement["fusion_secondary"], 4),
        "narrative_intensity_agreement": round(agreement[INTENSITY_AXIS], 4),
        "narrative_motif_agreement": round(agreement[NARRATIVE_AXIS], 4),
        "narrative_agreement": round(agreement[NARRATIVE_AXIS], 4),
        "theme_primary_threshold": theme_min,
        "presentation_threshold": presentation_min,
        "mean_tag_set_overlap": round(mean_overlap, 4),
        "tag_set_overlap_threshold": overlap_min,
        "overlap_formula": mode_cfg["overlap_formula"],
        "passed": passed_thresholds,
        "errors": errors,
        "colliding_theme_pairs": dict(colliding_pairs["theme_primary"]),
        "colliding_pairs_by_axis": {axis: dict(pairs) for axis, pairs in colliding_pairs.items()},
        "disagreements": disagreements,
        "low_confidence_products": low_conf,
        "comparisons": comparisons,
    }
    out_path = Path(args.output).resolve()
    write(out_path, report)
    print(json.dumps({
        "status": "passed" if passed_thresholds else "failed",
        "output": str(out_path),
        "theme_primary_agreement": report["theme_primary_agreement"],
        "presentation_agreement": report["presentation_agreement"],
        "fusion_agreement": report["fusion_agreement"],
        "narrative_intensity_agreement": report["narrative_intensity_agreement"],
        "narrative_motif_agreement": report["narrative_motif_agreement"],
        "narrative_agreement": report["narrative_agreement"],
        "colliding_pairs_by_axis": report["colliding_pairs_by_axis"],
        "mean_tag_set_overlap": report["mean_tag_set_overlap"],
        "disagreement_count": len(disagreements),
        "low_confidence_count": len(low_conf),
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0 if passed_thresholds else 2


def cmd_finalize(args) -> int:
    cfg = load_blacklist_cfg()
    vocab_raw = read(Path(args.vocab).resolve())
    vocab_entries = _extract_vocab_entries(vocab_raw)
    vocab_map = {e["tag_id"]: e for e in vocab_entries}
    tags_raw = read(Path(args.tags).resolve())
    products = deepcopy(_extract_product_list(tags_raw))
    qc = read(Path(args.qc).resolve())
    preset = qc.get("preset", "pilot")
    max_unresolved_low = int(cfg["mode_thresholds"][preset].get("max_unresolved_low_confidence", 0))

    if not qc.get("passed"):
        raise ValueError("cannot finalize tagged_corpus before blind QC passes its thresholds")

    adjudications = {}
    adj_raw = None
    if args.adjudications:
        adj_path = Path(args.adjudications).resolve()
        if adj_path.is_file():
            adj_raw = read(adj_path)
            adj_items = adj_raw.get("decisions", adj_raw) if isinstance(adj_raw, dict) else adj_raw
            if isinstance(adj_items, list):
                for item in adj_items:
                    uid = str(item.get("unified_app_id") or "")
                    if uid:
                        adjudications[uid] = item
            elif isinstance(adj_items, dict):
                adjudications = {
                    str(k): v for k, v in adj_items.items()
                    if adj_items is not adj_raw or k != "adjudicated_by"
                }

    required_matches = ("theme_match", "presentation_match", "fusion_match", "narrative_intensity_match")
    unresolved_diffs = [
        d["unified_app_id"] for d in qc.get("disagreements", [])
        if any(d.get(key) is False for key in required_matches)
        and not isinstance(adjudications.get(str(d["unified_app_id"])), dict)
    ]
    if unresolved_diffs:
        raise ValueError(f"unresolved blind disagreements require explicit adjudication: {unresolved_diffs}")

    applied_log = []
    unresolved_low = []
    for prod in products:
        uid = str(prod["unified_app_id"])
        if uid in adjudications:
            dec = adjudications[uid]
            if not isinstance(dec, dict):
                raise ValueError(f"{uid}: adjudication must be an object")
            dec_by = str(dec.get("adjudicated_by") or (adj_raw.get("adjudicated_by") if isinstance(adj_raw, dict) else "") or "human")
            if isinstance(dec.get("final_tags"), list) and dec["final_tags"]:
                for t in dec["final_tags"]:
                    if t.get("tag_id") not in vocab_map:
                        raise ValueError(f"adjudication uses unknown tag_id: {t.get('tag_id')}")
                prod["tags"] = dec["final_tags"]
            prod["adjudicated_by"] = dec_by
            prod["adjudicated_by_human"] = (dec_by.strip().lower() == "human")
            default_note = "人工复核确认" if prod["adjudicated_by_human"] else "复核确认"
            prod["adjudication_note"] = str(dec.get("note") or dec.get("decision") or default_note)
            if prod.get("confidence") == "low":
                prod["confidence"] = "adjudicated"
            applied_log.append({"unified_app_id": uid, "app_name": prod.get("app_name"), "note": prod["adjudication_note"]})
        if prod.get("confidence") == "low":
            unresolved_low.append(uid)
        prod[NARRATIVE_AXIS] = _checked_narrative(prod, cfg[NARRATIVE_AXIS]["controlled_enum"])
        prod["tags"] = _main_tags(prod)

    if len(unresolved_low) > max_unresolved_low:
        raise ValueError(f"unresolved low_confidence products remain ({len(unresolved_low)}): {unresolved_low}")

    corpus = {
        "schema_version": "1.0.0",
        "status": "verified",
        "preset": preset,
        "finalized_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vocab_approval": {
            "approved_by": vocab_raw.get("approved_by", "human") if isinstance(vocab_raw, dict) else "human",
            "approved_date": vocab_raw.get("approved_date", "") if isinstance(vocab_raw, dict) else "",
            "signoff_note": vocab_raw.get("signoff_note", "") if isinstance(vocab_raw, dict) else "",
        },
        "vocab": vocab_entries,
        "products": products,
        "qc_summary": {
            "protocol_disclosure": qc["protocol_disclosure"],
            "sample_evaluated": qc["sample_evaluated"],
            "theme_primary_agreement": qc["theme_primary_agreement"],
            "presentation_agreement": qc.get("presentation_agreement"),
            "fusion_agreement": qc.get("fusion_agreement"),
            "narrative_intensity_agreement": qc.get("narrative_intensity_agreement"),
            "narrative_agreement": qc.get("narrative_agreement"),
            "colliding_pairs_by_axis": qc.get("colliding_pairs_by_axis", {}),
            "mean_tag_set_overlap": qc["mean_tag_set_overlap"],
            "passed": True,
            "disagreement_count": len(qc.get("disagreements", [])),
            "comparisons": qc.get("comparisons", []),
        },
        "adjudications": applied_log,
    }
    out_path = Path(args.output).resolve()
    write(out_path, corpus)
    print(json.dumps({"status": "finalized", "output": str(out_path), "products": len(products),
                      "vocab": len(vocab_entries), "adjudications_applied": len(applied_log)}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 2 Tag QC for casual-tag-market-research")
    sub = parser.add_subparsers(dest="command", required=True)

    p_vv = sub.add_parser("validate-vocab", help="Validate controlled vocabulary size, format and blacklist")
    p_vv.add_argument("--vocab", required=True)
    p_vv.add_argument("--preset", choices=("pilot", "full"), required=True)
    p_vv.add_argument("--prior-vocab", default=None, help="Prior pilot vocab JSON to enforce frozen tag_ids")
    p_vv.set_defaults(func=cmd_validate_vocab)

    p_vt = sub.add_parser("validate-tags", help="Validate Pass 2 tags against slots, vocab and non-marketing evidence rules")
    p_vt.add_argument("--vocab", required=True)
    p_vt.add_argument("--tags", required=True)
    p_vt.add_argument("--roster", required=True)
    p_vt.add_argument("--bundle", required=True)
    p_vt.add_argument("--preset", choices=("pilot", "full"), required=True)
    p_vt.set_defaults(func=cmd_validate_tags)

    p_sp = sub.add_parser("sample", help="Generate stratified blind retest input with Pass 1/2 and Game IQ stripped")
    p_sp.add_argument("--bundle", required=True)
    p_sp.add_argument("--tags", required=True)
    p_sp.add_argument("--roster", required=True)
    p_sp.add_argument("--preset", choices=("pilot", "full"), required=True)
    p_sp.add_argument("--output", required=True)
    p_sp.add_argument("--seed", type=int, default=20261124)
    p_sp.set_defaults(func=cmd_sample)

    p_ev = sub.add_parser("evaluate", help="Evaluate blind retest consistency against agreement and Jaccard thresholds")
    p_ev.add_argument("--tags", required=True)
    p_ev.add_argument("--blind", required=True)
    p_ev.add_argument("--preset", choices=("pilot", "full"), required=True)
    p_ev.add_argument("--output", required=True)
    p_ev.set_defaults(func=cmd_evaluate)

    p_fn = sub.add_parser("finalize", help="Apply explicit adjudications and freeze tagged_corpus.json")
    p_fn.add_argument("--vocab", required=True)
    p_fn.add_argument("--tags", required=True)
    p_fn.add_argument("--qc", required=True)
    p_fn.add_argument("--adjudications", default=None)
    p_fn.add_argument("--output", required=True)
    p_fn.set_defaults(func=cmd_finalize)
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

"""Stage 2 Tag Quality Control: vocab gate, slot/evidence gate, blind sampling, consistency & finalization.

Pure Python standard library. Reads rules exclusively from configs/tag_blacklist.json.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import random
import re
import sys
from tempfile import NamedTemporaryFile

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import digest, read, write


NARRATIVE_AXIS = "narrative_motif"
INTENSITY_AXIS = "narrative_intensity"
OFFICIAL_MORPHEMES = ("复建", "复耕", "选装", "物业", "镇区", "影都", "复苏", "矩阵", "体系")
COMPOUND_LABEL_RE = re.compile(r"[/／、()（）\[\]［］【】或]")
NEXT_ACTIONS = {
    "validate-vocab": "按 errors 修订 --vocab，或核对 --roster、--pass1 与 --gaps 指向的名单、逐图记录和补图记录，再运行 validate-vocab。",
    "validate-tags": "按 errors 核对标签、证据和呈现判定单元，补齐所需字段后重新运行 tag_qc.py validate-tags。",
    "evaluate": "按 errors 核对 --blind 的协议披露、模型记录与样本，或按报告阈值复核 --tags 与 --blind。改完重新运行本次 tag_qc.py evaluate 命令。",
    "finalize": "按 errors 补齐 --adjudications 的正式裁决与叙事证据，若盲测未通过或披露有误则先用 evaluate 重出 --qc。改完重新运行本次 tag_qc.py finalize 命令。",
}


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


def _narrative_copy_errors(prod, tags):
    evidence = prod.get("narrative_evidence")
    if not isinstance(evidence, list):
        return []

    presentation_quotes = {
        "".join(quote.split()).casefold()
        for tag in tags
        if tag.get("slot") == "presentation"
        and isinstance(quote := tag.get("evidence_quote"), str)
        and quote.strip()
    }
    errors = []
    uid = prod["unified_app_id"]
    for index, item in enumerate(evidence):
        quote = item.get("evidence_quote") if isinstance(item, dict) else None
        if isinstance(quote, str) and "".join(quote.split()).casefold() in presentation_quotes:
            errors.append(
                f"{uid}: narrative_evidence[{index}].evidence_quote duplicates presentation evidence_quote"
            )
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


def _nonempty(value):
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def _presentation_vocab_errors(item, forbidden_terms, dimension_tokens, dimension_re):
    tid = str(item.get("tag_id") or "")
    errors = []
    for field in ("definition", "include_examples"):
        value = item.get(field)
        texts = value if isinstance(value, list) else [value]
        normalized = [normalize_term(text) for text in texts]
        for term, original in forbidden_terms.items():
            if any(term in text for text in normalized):
                errors.append(f"{tid}: cross-axis term '{original}' in presentation {field}")

    if item.get("render_tech_verified") is True and _nonempty(item.get("render_tech_evidence")):
        return errors
    fields = []
    if dimension_tokens.intersection(tid.casefold().split("_")):
        fields.append("tag_id")
    fields.extend(
        field for field in ("label_zh", "label_en")
        if dimension_re and dimension_re.search(str(item.get(field) or ""))
    )
    if fields:
        errors.append(
            f"{tid}: presentation dimension token in {', '.join(fields)} requires "
            "render_tech_verified=true and nonempty render_tech_evidence"
        )
    return errors


def _irreducibility_errors(tid, note, hits):
    if not isinstance(note, dict):
        return [f"{tid}: irreducibility_note requires groups and products arrays"]
    errors = []
    placeholders = re.compile(r"同上|如上|同前|见上|\b(?:same\s+as\s+above|ditto)\b", re.IGNORECASE)
    for level in ("groups", "products"):
        rows = note.get(level)
        label = f"{tid}: irreducibility_note.{level}"
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            errors.append(f"{label} must be a nonempty array of objects")
            continue
        covered = set()
        for row in rows:
            placeholder = placeholders.search(json.dumps(row, ensure_ascii=False))
            if placeholder:
                errors.append(f"{label} contains placeholder {placeholder[0]!r}")
            if level == "groups":
                members = row.get("hit_app_ids")
                if not isinstance(members, list) or not members:
                    errors.append(f"{label} requires hit_app_ids for each group")
                    continue
            else:
                members = [row.get("unified_app_id")]
            covered.update(str(uid) for uid in members)
        if covered != hits:
            errors.append(f"{label} must cover hit_app_ids: missing {sorted(hits - covered)}, extra {sorted(covered - hits)}")
    return errors


def _valid_screenshot_review(review, file_count, kinds):
    return isinstance(review, list) and len(review) == file_count and all(
        isinstance(shot, dict) and type(shot.get("screenshot_index")) is int
        and shot["screenshot_index"] == index and shot.get("image_kind") in kinds
        for index, shot in enumerate(review, 1)
    )


def _vocab_pass1_audit(args, roster, pass1, visual_policy, errors):
    reviews, units = {}, {}
    for row in _extract_product_list(pass1):
        uid = str(row["unified_app_id"])
        if uid in reviews or uid not in roster:
            errors.append(f"{uid}: duplicate Pass 1 record or product outside --roster")
        reviews[uid] = row
    totals, missing_halves, zero_gameplay = Counter(), defaultdict(set), set()
    kinds = visual_policy["image_kind_enum"]
    for uid, product in roster.items():
        genre = product["primary_subgenre"]
        totals[genre] += 1
        row = reviews.get(uid)
        if row is None:
            errors.append(f"{uid}: --pass1 is missing screenshots_reviewed and screenshot_review")
            continue
        files = product.get("screenshot_files", [])
        if row.get("screenshots_reviewed") != files or len(set(files)) != len(files):
            errors.append(f"{uid}: screenshots_reviewed must match all roster screenshot_files in order without duplicates")
        review = row.get("screenshot_review")
        if not _valid_screenshot_review(review, len(files), kinds):
            errors.append(f"{uid}: screenshot_review must list indices 1 through {len(files)} in order with a valid image_kind")
            continue
        if all(shot["image_kind"] in visual_policy["forbidden_presentation_image_kinds"] for shot in review):
            zero_gameplay.add(uid)
            units[uid] = "promo_or_portrait_only"
            continue
        unit = row.get("visual_unit") or next(
            (tag["visual_unit"] for tag in row.get("free_tags", [])
             if tag.get("axis") == "presentation" and tag.get("visual_unit")),
            None,
        )
        if unit not in visual_policy["controlled_enum"] or unit == "promo_or_portrait_only":
            errors.append(f"{uid}: --pass1 requires a gameplay visual_unit for reviewed gameplay")
        units[uid] = unit
        if genre in ("merge", "match_3") and unit in ("gameplay_board", "main_scene"):
            missing_halves[genre].add(uid)

    _check_l3_supplements(args, totals, missing_halves, errors)
    return reviews, units, zero_gameplay, {
        genre: {"roster_count": totals[genre], "L3": len(missing_halves[genre]),
                "missing_half_app_ids": sorted(missing_halves[genre])}
        for genre in ("merge", "match_3") if totals[genre]
    }


def _check_l3_supplements(args, totals, missing_halves, errors):
    over_limit = {genre: ids for genre, ids in missing_halves.items() if len(ids) * 10 > totals[genre] * 3}
    if over_limit:
        gap_path = Path(args.gaps) if args.gaps else Path(args.roster).with_name("gap_register.json")
        gap_data = read(gap_path) if args.gaps or gap_path.is_file() else {"gaps": []}
        gaps = gap_data["gaps"] if isinstance(gap_data, dict) else gap_data
        supplemented = {
            str(gap.get("unified_app_id")) for gap in gaps
            if gap.get("impact_level") == "L3" and _nonempty(gap.get("supplement_source"))
            and _nonempty(gap.get("supplement_result"))
        }
        for genre, ids in sorted(over_limit.items()):
            missing = sorted(ids - supplemented)
            if missing:
                errors.append(f"{genre}: L3 缺半边 {len(ids)}/{totals[genre]}，缺补图记录 ({gap_path}): {', '.join(missing)}")


def _valid_screenshot_index(value):
    indices = value if isinstance(value, list) else [value]
    return bool(indices) and all(type(index) is int and index > 0 for index in indices)


def _visual_confidence_error(tag, uid, confidence, cfg):
    unit = tag.get("visual_unit")
    if unit in cfg["visual_unit_policy"]["forbidden_high_conf_units"] and confidence != "low":
        return f"{uid}: presentation visual_unit {unit} requires confidence=low"
    return None


def _promo_presentation_error(tag, uid, confidence, review, visual_policy):
    screenshot_index = tag.get("screenshot_index")
    indices = screenshot_index if isinstance(screenshot_index, list) else [screenshot_index]
    if review:
        reviewed_indices = {shot["screenshot_index"] for shot in review if type(shot.get("screenshot_index")) is int}
        if not _valid_screenshot_index(screenshot_index) or any(index not in reviewed_indices for index in indices):
            return f"{uid}/{tag.get('tag_id')}: presentation screenshot_index {indices} outside screenshot_review"
    if tag.get("visual_unit") == "promo_or_portrait_only" and confidence == "low":
        return None

    forbidden_kinds = visual_policy["forbidden_presentation_image_kinds"]
    forbidden_indices = [
        shot["screenshot_index"]
        for shot in review
        if shot.get("image_kind") in forbidden_kinds
        and type(shot.get("screenshot_index")) is int
        and shot["screenshot_index"] in indices
    ]
    if forbidden_indices or tag.get("image_kind") in forbidden_kinds:
        indices = forbidden_indices or indices
        return f"{uid}/{tag.get('tag_id')}: presentation screenshot_index {indices} uses promo_only evidence"
    return None


def _protocol_disclosure_error(disclosure, record, protocol):
    if not isinstance(disclosure, str) or not disclosure.strip():
        return "protocol_disclosure must be nonempty text"
    if isinstance(record, dict) and record:
        return None
    allowed_disclosures = protocol.get("allowed_disclosures", [protocol["disclosure_label"]])
    if disclosure.strip() in allowed_disclosures:
        return None
    markers = [*protocol["unverified_model_name_patterns"], "未核实与主会话同模型", "跨模型"]
    hits = [marker for marker in markers if marker.casefold() in disclosure.casefold()]
    if hits:
        return f"protocol_disclosure contains unverified model or cross-model claims {hits!r}; requires nonempty harness_model_record"
    return None


def _validate_vocab(args):
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

    vocab_path = Path(args.vocab).resolve()
    vocab_raw = read(vocab_path)
    entries = _extract_vocab_entries(vocab_raw)
    metadata = vocab_raw if isinstance(vocab_raw, dict) else {}
    errors, warnings = [], []
    warnings_structured = []

    def warn(tid, warning_type, message=None):
        warnings.append(f"{tid}: {message or warning_type}")
        warnings_structured.append({"tag_id": tid, "warning_type": warning_type, "message": message or warning_type})

    roster_bytes = Path(args.roster).read_bytes() if args.roster else None
    roster_doc = json.loads(roster_bytes.decode("utf-8-sig")) if args.roster else None
    roster = {str(row["unified_app_id"]): row for row in _extract_product_list(roster_doc)} if args.roster else {}
    if args.pass1 and not args.roster:
        errors.append("--pass1 requires --roster for screenshot coverage")
    zero_gameplay, l3_by_subgenre = set(), {}
    reviews, units = {}, {}
    if args.pass1 and args.roster:
        if not isinstance(vocab_raw, dict):
            errors.append("--vocab must be an object to write script_gate_receipt")
        pass1_bytes = Path(args.pass1).read_bytes()
        reviews, units, zero_gameplay, l3_by_subgenre = _vocab_pass1_audit(
            args, roster, json.loads(pass1_bytes.decode("utf-8-sig")), cfg["visual_unit_policy"], errors,
        )

    policy = cfg["presentation_policy"]
    dimension_tokens = {token.casefold() for token in policy["forbid_dimension_tokens_in_label_or_id"]}
    dimension_pattern = "|".join(re.escape(token) for token in sorted(dimension_tokens))
    # ASCII word boundaries let Chinese text directly precede a dimension token.
    dimension_re = re.compile(rf"\b(?:{dimension_pattern})\b", re.IGNORECASE | re.ASCII) if dimension_pattern else None
    cross_axis_terms = list(axis_forbidden.get("presentation", []))
    cross_axis_terms.extend(item.get("label_zh") for item in entries if item.get("axis") == "theme_primary")
    forbidden_terms = {normalize_term(term): term for term in cross_axis_terms if normalize_term(term)}

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
            if len(zh) < fmt["label_zh_min_chars"]:
                errors.append(f"{tid}: label_zh '{zh}' requires at least {fmt['label_zh_min_chars']} characters")
            if len(zh) > int(fmt["label_zh_hard_max_chars"]):
                errors.append(f"{tid}: label_zh '{zh}' exceeds hard max {fmt['label_zh_hard_max_chars']} chars")
            elif len(zh) > int(fmt["label_zh_recommended_max_chars"]):
                warn(tid, "exceeds recommended", f"label_zh '{zh}' exceeds recommended {fmt['label_zh_recommended_max_chars']} chars")
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
        hits = set()
        if not isinstance(hit_ids, list):
            errors.append(f"{tid}: hit_app_ids must be an array")
        else:
            hits = {str(uid).strip() for uid in hit_ids if uid is not None and str(uid).strip()}
            hits_by_id[tid] = hits
            if axis in axes:
                if len(hits) != len(hit_ids):
                    errors.append(f"{tid}: {axis} hit_app_ids contains duplicate or empty IDs")
                for uid in sorted(hits & hits_by_axis[axis]):
                    errors.append(f"{tid}: {axis} hit_app_ids overlap for {uid}")
            if args.roster:
                for uid in sorted(hits - roster.keys()):
                    errors.append(f"{tid}: {axis} hit_app_ids contains {uid} outside --roster")
            hits_by_axis[axis].update(hits)
            if axis in axes and not hits:
                errors.append(f"{tid}: hit_app_ids must contain at least one app ID")
            if len(hits) == 1 and item.get("emerging") is not True:
                warn(tid, "one hit without emerging=true")
        if item.get("emerging") is True:
            if not args.roster:
                errors.append(f"{tid}: emerging=true requires --roster")
            elif args.preset == "full" and len(hits) >= cfg["emerging_policy"]["revert_to_false_on_full_hits_min"]:
                errors.append(f"{tid}: emerging must be false in full after {len(hits)} hits")
            elif len(hits) != 1:
                errors.append(f"{tid}: emerging=true requires exactly one hit")
            else:
                uid = next(iter(hits))
                app = roster.get(uid, {})
                window = (roster_doc.get("new_release_window") or {}) if isinstance(roster_doc, dict) else {}
                try:
                    start, end, launch = (date.fromisoformat(value) for value in (
                        window.get("start"), window.get("end"), app.get("effective_launch_date")
                    ))
                except (TypeError, ValueError):
                    errors.append(f"{tid}: emerging hit {uid} requires an effective_launch_date and new_release_window dates")
                else:
                    if app.get("is_new_release") is not True or not start <= launch <= end:
                        errors.append(f"{tid}: emerging hit {uid} must be a verified new release within new_release_window")
        if fmt.get("require_definition", True) and not str(item.get("definition") or "").strip():
            errors.append(f"{tid}: missing definition")
        if fmt.get("require_include_and_exclude_examples", True):
            inc = item.get("include_examples")
            exc = item.get("exclude_examples")
            if not inc or (isinstance(inc, list) and not any(str(x).strip() for x in inc)):
                errors.append(f"{tid}: missing include_examples")
            if not exc or (isinstance(exc, list) and not any(str(x).strip() for x in exc)):
                errors.append(f"{tid}: missing exclude_examples")
        for field in ("include_examples", "exclude_examples"):
            examples = item.get(field) or []
            if not isinstance(examples, list):
                errors.append(f"{tid}: {field} must be an array")
                continue
            for example in examples:
                uid = example.get("unified_app_id") if isinstance(example, dict) else None
                in_hits = uid is not None and str(uid) in hits
                if in_hits != (field == "include_examples"):
                    errors.append(f"{tid}: {field} unified_app_id {uid!r} conflicts with hit_app_ids")

        for field_name, val in (("tag_id", tid), ("label_zh", zh), ("label_en", en)):
            normalized = normalize_term(val)
            if normalized in banned:
                errors.append(f"{tid}: banned generic term hit in {field_name}='{val}'")
            if COMPOUND_LABEL_RE.search(val):
                errors.append(f"{tid}: compound label in {field_name}='{val}'")
            for term in axis_forbidden.get(axis, []):
                if normalize_term(term) in normalized:
                    errors.append(f"{tid}: axis_forbidden_terms hit '{term}' in {field_name}='{val}'")
        if axis == "presentation":
            errors.extend(_presentation_vocab_errors(item, forbidden_terms, dimension_tokens, dimension_re))
        matched = [term for term in morphemes if normalize_term(term) in normalize_term(zh)]
        if matched:
            warn(tid, "official morphemes in label_zh", f"official morphemes in label_zh: {'、'.join(matched)}")

    if args.roster:
        for axis in ("theme_primary", "presentation"):
            for uid in sorted(roster.keys() - hits_by_axis[axis]):
                errors.append(f"{axis} coverage missing roster product {uid}")
    hits_by_axis["presentation"].difference_update(zero_gameplay)
    concentration = cfg["concentration_policy"]
    presentation_max_share = 0.0
    concentration_gate_status = "passed"
    concentration_statuses = (
        "passed", "passed_with_irreducibility_note", "passed_with_concentration_waiver",
        "failed_irreducibility_note", "failed_missing_concentration_waiver",
    )
    for tid, item in entry_map.items():
        axis = item.get("axis")
        if axis not in concentration["axes"]:
            continue
        hit_ids = hits_by_id.get(tid, set())
        hits = len(hit_ids & hits_by_axis[axis])
        axis_total = len(hits_by_axis[axis])
        share = hits / axis_total if axis_total else 0.0
        if axis == "presentation":
            presentation_max_share = max(presentation_max_share, share)
        warning_threshold = policy["max_share_warning"]
        if share <= warning_threshold:
            continue
        share_label = f"{axis} 单个标签占比 {hits}/{axis_total} ({share:.1%})"
        eligible = hits >= concentration["min_hits"] or axis_total >= concentration["min_axis_denominator"]
        if not eligible:
            warn(tid, "small_denominator", f"{share_label}，小分母，未达集中度核验门槛")
            continue
        warn(tid, f"{axis} hit share", f"{share_label} 超过 {warning_threshold:.0%}")
        note = item.get("irreducibility_note")
        if note is None:
            note_errors = [f"{tid}: {share_label}，缺少 irreducibility_note（两层不可再分表）"]
        else:
            note_errors = _irreducibility_errors(tid, note, hit_ids)
        errors.extend(note_errors)
        gate_status = "failed_irreducibility_note" if note_errors else "passed_with_irreducibility_note"
        if share > policy["max_share_hard_block_without_waiver"]:
            if any(_nonempty(record.get("concentration_waiver")) for record in (item, metadata)):
                if not note_errors:
                    gate_status = "passed_with_concentration_waiver"
            else:
                errors.append(f"{tid}: {share_label} 须有人工 concentration_waiver")
                gate_status = "failed_missing_concentration_waiver"
        concentration_gate_status = max(concentration_gate_status, gate_status, key=concentration_statuses.index)

    fusion_count = sum(item.get("axis") == "fusion_secondary" for item in entries)
    fusion_audit = metadata.get("fusion_zero_audit")
    if not fusion_count and not (
        (isinstance(fusion_audit, (list, dict)) and fusion_audit) or _nonempty(metadata.get("fusion_disposition"))
    ):
        errors.append("zero fusion_secondary tags require nonempty fusion_zero_audit or fusion_disposition")

    # Freeze existing pilot tag_ids when expanding to full
    if args.prior_vocab and mode_cfg.get("freeze_existing_pilot_tag_ids_on_expand", False):
        prior_entries = _extract_vocab_entries(read(Path(args.prior_vocab).resolve()))
        for pitem in prior_entries:
            ptid = pitem.get("tag_id")
            if ptid not in entry_map:
                errors.append(f"frozen prior tag_id missing on expand: {ptid}")
            elif entry_map[ptid].get("axis") != pitem.get("axis"):
                errors.append(f"frozen prior tag_id axis changed: {ptid}")

    if not errors and args.roster and args.pass1:
        unsigned = {key: value for key, value in vocab_raw.items() if key != "script_gate_receipt"}
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        vocab_raw["script_gate_receipt"] = {
            "script_version": "v0.2.0",
            "command": args.command,
            "exit_code": 0,
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "input_sha256": {
                "vocab": digest(canonical.encode("utf-8")),
                "roster": digest(roster_bytes),
                "pass1": digest(pass1_bytes),
            },
        }
        write(vocab_path, vocab_raw)

    result = {
        "status": "passed" if not errors else "failed",
        "preset": args.preset,
        "vocab_count": main_count,
        "presentation_max_share": presentation_max_share,
        "concentration_gate_status": concentration_gate_status,
        "fusion_candidates_retained": fusion_count,
        "hit_counts": {tid: len(hits) for tid, hits in hits_by_id.items()},
        "errors": errors,
        "warnings": warnings,
        "warnings_structured": warnings_structured,
        "l3_by_subgenre": l3_by_subgenre,
    }
    if errors:
        result["next_action"] = NEXT_ACTIONS["validate-vocab"]
    return result, (vocab_raw, roster, reviews, units, hits_by_id, hits_by_axis, cfg)


def cmd_validate_vocab(args) -> int:
    result, _ = _validate_vocab(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["errors"] else 0


def _signoff_diff(previous, entries, hits_by_id):
    old = {item["tag_id"]: item for item in _extract_vocab_entries(previous)}
    removed = old.keys() - entries.keys()
    merged = defaultdict(list)
    for tid in sorted(removed):
        hits = {str(uid).strip() for uid in old[tid].get("hit_app_ids", [])}
        targets = [target for target, item in entries.items()
                   if hits and hits <= hits_by_id[target] and old[tid]["axis"] == item["axis"]]
        if len(targets) == 1:
            merged[targets[0]].append(tid)
    merged = {target: sources for target, sources in merged.items() if target in old or len(sources) > 1}
    absorbed = {tid for sources in merged.values() for tid in sources}
    rows = [("增", tid, entries[tid]["label_zh"]) for tid in sorted(entries.keys() - old.keys() - merged.keys())]
    rows.extend(("删", tid, old[tid]["label_zh"]) for tid in sorted(removed - absorbed))
    for tid in sorted(old.keys() & entries.keys()):
        before, after = old[tid], entries[tid]
        fields = sorted(field for field in before.keys() | after.keys()
                        if field != "hit_app_ids" and before.get(field) != after.get(field))
        old_hits = {str(uid).strip() for uid in before.get("hit_app_ids", [])}
        added, dropped = sorted(hits_by_id[tid] - old_hits), sorted(old_hits - hits_by_id[tid])
        if fields or added or dropped:
            rows.append(("改", tid, f"{before['label_zh']} → {after['label_zh']}。"
                         f"变更字段 {'、'.join(fields) or '—'}。新增命中 {', '.join(added) or '—'}，"
                         f"移除命中 {', '.join(dropped) or '—'}"))
    rows.extend(("并", target, f"{', '.join(merged[target])} → {target}（按同轴命中归并推定）")
                for target in sorted(merged))
    return rows


def _signoff_table(title, headers, rows, markdown=False):
    from report_view import E

    if markdown:
        def line(values):
            return "| " + " | ".join(E(value).replace("|", "&#124;").replace("\n", " / ") for value in values) + " |\n"
        return f"### {title}\n\n" + line(headers) + line(["---"] * len(headers)) + "".join(map(line, rows)) + "\n"

    def row(tag, values):
        return "<tr>" + "".join(f"<{tag}>{E(value).replace(chr(10), '<br>')}</{tag}>" for value in values) + "</tr>"
    return (f'<div class="table-wrap"><table class="data-table"><caption>{title}</caption>'
            f'<thead>{row("th", headers)}</thead><tbody>{"".join(row("td", values) for values in rows)}</tbody></table></div>')


def _signoff_sample_ids(roster, units, l3_ids, changed_ids):
    ids = sorted(roster, key=lambda uid: (roster[uid].get("subgenre_rank") or float("inf"), uid))
    priorities = [ids[0]] if ids else []
    for candidates in ({uid for uid in ids if roster[uid].get("is_boundary_seat")},
                       {uid for uid in ids if units[uid] == "promo_or_portrait_only"} | l3_ids, changed_ids):
        candidate = next((uid for uid in ids if uid in candidates and uid not in priorities), None)
        if candidate is not None:
            priorities.append(candidate)
    return list(dict.fromkeys(priorities + ids))[:5]


def _write_signoff(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temp_path = Path(staged.name)
    try:
        with staged:
            staged.write(content)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def cmd_render_signoff(args) -> int:
    from report_view import E, STYLE

    draft = Path(args.vocab).resolve()
    output_dir = draft.parents[2] if len(draft.parents) > 2 else draft.parent
    output = Path(args.output).resolve() if args.output else output_dir / "tag_vocab_signoff.html"
    summary_path = Path(args.summary_md).resolve() if args.summary_md else draft.with_suffix(".md")
    gap_path = Path(args.gaps).resolve() if args.gaps else Path(args.roster).resolve().with_name("gap_register.json")
    inputs = {Path(path).resolve() for path in (draft, args.roster, args.pass1, args.prior_vocab, args.prev_vocab, gap_path) if path}
    if output == summary_path or {output, summary_path} & inputs:
        raise ValueError("signoff outputs must be distinct from each other and from the inputs")

    result, (vocab, roster, reviews, units, hits_by_id, hits_by_axis, cfg) = _validate_vocab(args)
    if result["errors"]:
        try:
            output.unlink(missing_ok=True)
        finally:
            summary_path.unlink(missing_ok=True)
        print(json.dumps(result, ensure_ascii=False))
        return 2

    receipt = vocab["script_gate_receipt"]
    hashes = {"draft_vocab_sha256": digest(draft.read_bytes()), "roster_sha256": receipt["input_sha256"]["roster"],
              "pass1_sha256": receipt["input_sha256"]["pass1"]}
    entries = {item["tag_id"]: item for item in _extract_vocab_entries(vocab)}
    diff = _signoff_diff(read(args.prev_vocab), entries, hits_by_id) if args.prev_vocab else []
    diff_html = ('<section class="dash-section" id="changes"><h2>轮次 diff</h2>'
                 + (_signoff_table("词条变化", ("变化", "tag_id", "说明"), diff) if diff else "<p>词条无变化。</p>")
                 + "</section>") if args.prev_vocab else ""
    changed_ids = {uid for _, tid, _ in diff for uid in hits_by_id.get(tid, ())}
    axes = {"theme_primary": "主题材", "presentation": "呈现", "fusion_secondary": "融合"}
    domains = {domain["id"]: domain["label_zh"] for domain in cfg["primary_domains"]}
    axis_tables, matches = [], defaultdict(dict)
    concentration = {"groups": [], "products": []}
    for axis, label in axes.items():
        rows, denominator = [], len(hits_by_axis[axis])
        for tid, item in sorted(entries.items()):
            if item["axis"] != axis:
                continue
            hits = hits_by_id[tid] & hits_by_axis[axis]
            primary = domains.get(item.get("parent_domain"), "") if axis == "theme_primary" else ""
            share = f"{len(hits) / denominator:.1%}" if denominator else "—"
            examples = [json.dumps(item.get(field, []), ensure_ascii=False) for field in ("include_examples", "exclude_examples")]
            rows.append((tid, primary, item["label_zh"], item.get("definition", ""), len(hits), share,
                         *examples, str(item.get("emerging") is True).lower()))
            for uid in hits:
                matches[uid][axis] = f"{primary} › {item['label_zh']}" if primary else item["label_zh"]
            for level, records in concentration.items():
                note = item.get("irreducibility_note") or {}
                records.extend((tid, json.dumps(row, ensure_ascii=False)) for row in note.get(level, []))
        axis_tables.append((f"{label}（占比分母 {denominator} 款）",
                            ("tag_id", "一级", "短词", "定义", "命中数", "占比", "纳入例", "排除例", "emerging"), rows))

    dispositions = {"exceeds recommended": "保留当前短词或缩短词名", "one hit without emerging=true": "核对实证后决定保留或归入近义标签",
                    "official morphemes in label_zh": "核对词名是否需要改为具体对象", "small_denominator": "记录小分母，扩样后复核"}
    warnings = result["warnings_structured"]
    warning_table = ("告警", ("词条、告警原文与处置",), [
        (f"{w['tag_id']}｜{w['message']}\n处置  {dispositions.get(w['warning_type'], '复核两层集中度说明，裁定保留或拆分标签')}",)
        for w in warnings
    ])
    l3 = result["l3_by_subgenre"]
    l3_ids = {uid for info in l3.values() for uid in info["missing_half_app_ids"]}
    units_by_genre = defaultdict(Counter)
    for uid, unit in units.items():
        units_by_genre[roster[uid]["primary_subgenre"]][unit] += 1
    gap_data = read(gap_path) if args.gaps or gap_path.is_file() else []
    gaps = gap_data["gaps"] if isinstance(gap_data, dict) else gap_data
    enum = cfg["visual_unit_policy"]["controlled_enum"]
    unit_rows = [(genre, *(counts[unit] for unit in enum),
                  f"{l3[genre]['L3']}/{l3[genre]['roster_count']} "
                  + ", ".join(l3[genre]["missing_half_app_ids"]) if genre in l3 else "—",
                  ", ".join(sorted(uid for uid in roster if roster[uid]["primary_subgenre"] == genre
                                   and units[uid] == "promo_or_portrait_only")) or "—",
                  " / ".join(f"{g['unified_app_id']} {g.get('supplement_source', '')} {g.get('supplement_result', '')}"
                             for g in gaps if str(g.get("unified_app_id")) in roster
                             and roster[str(g["unified_app_id"])]["primary_subgenre"] == genre
                             and g.get("impact_level") in ("L2", "L3")) or "—")
                 for genre, counts in sorted(units_by_genre.items())]
    unit_table = ("判定单元与 L3", ("品类", *enum, "L3 缺半边 ID", "L2 零实机图 ID", "补图来源与结果"), unit_rows)
    samples = []
    for uid in _signoff_sample_ids(roster, units, l3_ids, changed_ids):
        review, tagged = reviews[uid], matches[uid]
        free_tags = review.get("free_tags", [])
        presentation_tags = [tag for tag in free_tags if tag.get("axis") == "presentation"]
        visual_fields = {
            field: review.get(field) or next((tag[field] for tag in presentation_tags if tag.get(field)), None)
            for field in ("screenshot_index", "primary_visual_region", "visual_unit_fallback_reason")
        }
        evidence = " / ".join(f"{tag.get('evidence_source_id') or tag.get('evidence_type') or 'Pass 1'} {tag['evidence_quote']}"
                              for tag in free_tags if tag.get("evidence_quote"))
        samples.append((f"{roster[uid].get('app_name') or uid} · {uid}",
                        "，".join(f"{label} {tagged.get(axis, '留空（缺合格实机图）' if axis == 'presentation' else '留空（未命中独立循环）')}"
                                  for axis, label in axes.items()),
                        f"叙事 {review.get('narrative_intensity') or '未记录'}／{review.get('narrative_motif') or '—'}。"
                        f"判定单元 {units[uid]}。截图 {visual_fields['screenshot_index'] or '—'}。"
                        f"观察区域 {visual_fields['primary_visual_region'] or '—'}。"
                        f"{visual_fields['visual_unit_fallback_reason'] or ''} {evidence or '已阅图源 ' + ', '.join(review['screenshots_reviewed'])}"))
    coverage = f"融合轴命中 {len(hits_by_axis['fusion_secondary'])}/{len(roster)} 款，缺独立玩法实证的产品可留空。"
    decisions = [
        ("词条边界", f"草稿收录 {result['vocab_count']} 词。相近词的纳入边界将决定预标归类。",
         "沿用当前定义与例子", "指定词条，修改边界后重验", "axes"),
        ("告警词处置", f"脚本报出 {len(warnings)} 条告警。保留告警词需要结合用词与命中实证。",
         "按附录逐条确认", "指定告警词，改名或归入近义标签", "warnings"),
        ("集中标签取舍", f"单个呈现标签占比最高 {result['presentation_max_share']:.1%}。拆分会改变覆盖口径。",
         "按现有两层说明确认标签粒度", "指定要拆分的标签和划分依据", "concentration"),
        ("融合覆盖范围", coverage + "新增融合标签需要可复核的独立玩法证据。",
         "沿用当前覆盖", "指定产品，补独立循环实证后复核", "fusion_zero_audit"),
    ]
    cards, decision_md, decision_data = [], [], []
    for number, (title, reason, default, alternative, anchor) in enumerate(decisions, 1):
        response = f"第 {number} 项选默认／备选，涉及的词条或依据是……"
        body = f"{reason} 默认 {default}。备选 {alternative}。可回复「{response}」。"
        cards.append(f'<article class="vocab-card"><h3>{number}. {title}</h3><p>{body} <a href="#{anchor}">查看依据</a></p></article>')
        decision_md.append(f"{number}. **{title}**。{body}\n")
        decision_data.append({"number": number, "title": title, "options": {"default": default, "alternative": alternative},
                              "anchor": anchor})
    count = result["fusion_candidates_retained"]
    audit = (f"本轮保留 {count} 词，未触发归零。" if count else
             json.dumps(vocab.get("fusion_zero_audit") or vocab["fusion_disposition"], ensure_ascii=False, indent=2))
    meta = "".join(f'<meta name="{name}" content="{value}">\n' for name, value in hashes.items())
    hash_lines = "\n".join(f"{name} {value}" for name, value in hashes.items())
    sample_cards = "".join(f'<article class="vocab-card" data-sample-card><h3>{E(title)}</h3><p>{E(tags)}</p><p>{E(source)}</p></article>'
                           for title, tags, source in samples)
    tables = "".join(f"<h3>{title}</h3>{_signoff_table(title, headers, rows)}" for title, headers, rows in axis_tables)
    groups = _signoff_table("集中度分组论证", ("tag_id", "判据与依据"), concentration["groups"])
    products = _signoff_table("集中度逐款明细", ("tag_id", "产品与依据"), concentration["products"])
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}<title>词表签字</title><style>{STYLE}</style></head>
<body><main class="dash-main" style="margin:20px auto;padding:0 12px">
<header class="dash-section"><h1>词表签字</h1><p>本轮范围 {args.preset} · 名单 {len(roster)} 款 · {coverage}</p>
{'' if args.prev_vocab else '<p class="muted">首轮无前稿</p>'}
<p><a href="#script_gate_receipt">查看脚本绿灯回执</a></p><pre>{hash_lines}</pre>
<details id="script_gate_receipt"><summary>脚本绿灯回执</summary><pre>{E(json.dumps(receipt, ensure_ascii=False, indent=2))}</pre></details></header>
<section class="dash-section"><h2>决策卡</h2>{''.join(cards)}</section>{diff_html}
<section class="dash-section"><h2>{len(samples)} 款样例</h2>{sample_cards}</section>
<section class="dash-section" id="axes"><h2>三轴总览</h2>{tables}</section>
<details class="dash-section"><summary>附录</summary><h3 id="warnings">告警与处置</h3>{_signoff_table(*warning_table)}
<h3 id="concentration">两层集中度</h3>{groups}{products}<h3>判定单元与 L3</h3>{_signoff_table(*unit_table)}
<h3 id="fusion_zero_audit">融合归零审计</h3><pre>{E(audit)}</pre></details></main></body></html>"""
    markdown = (f"# 词表签字 · {args.preset}\n\n{coverage}\n\n" + "\n".join(f"- {line}" for line in hash_lines.splitlines())
                + "\n\n## 三轴总览\n\n" + "".join(_signoff_table(*table, markdown=True) for table in axis_tables)
                + "## 告警\n\n" + _signoff_table(*warning_table, markdown=True) + "## 判定单元与 L3\n\n"
                + _signoff_table(*unit_table, markdown=True) + f"## {len(samples)} 款样例\n\n"
                + "".join(f"### {E(title)}\n\n{E(tags)}\n\n{E(source)}\n\n" for title, tags, source in samples)
                + "## 拍板问题\n\n" + "".join(decision_md))
    html = re.sub(r"localstorage", lambda match: f"&#{ord(match[0][0])};{match[0][1:]}", html, flags=re.I)
    _write_signoff(summary_path, markdown)
    _write_signoff(output, html)
    print(json.dumps({"decision_count": len(decisions), "decisions": decision_data, "warning_count": len(warnings), **hashes,
                      "output": str(output), "summary_md": str(summary_path)}, ensure_ascii=False))
    return 0


def cmd_validate_tags(args) -> int:
    cfg = load_blacklist_cfg()
    slots_cfg = cfg["slots"]
    ev_cfg = cfg["evidence_rules"]
    visual_policy = cfg["visual_unit_policy"]
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
    if args.pass1:
        pass1 = read(Path(args.pass1).resolve())
        _vocab_pass1_audit(args, roster_map, pass1, visual_policy, errors)
    tagged_uids = set()
    visual_units = Counter()
    totals = Counter(product.get("primary_subgenre") for product in roster_map.values())
    missing_halves = defaultdict(set)
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
        errors.extend(_narrative_copy_errors(prod, raw_tags))

        roster_prod = roster_map.get(uid, {})
        available_shots = roster_prod.get("screenshot_files") or []
        genre = roster_prod.get("primary_subgenre")
        preferred_unit = visual_policy["preferred_unit_by_subgenre"].get(genre or prod.get("subgenre_id"))
        shots_rev = prod.get("screenshots_reviewed")
        if shots_rev != available_shots:
            errors.append(f"{pname}: screenshots_reviewed must match all roster screenshot_files in order")
        if not isinstance(shots_rev, list):
            shots_rev = []

        review = prod.get("screenshot_review")
        if not _valid_screenshot_review(review, len(available_shots), visual_policy["image_kind_enum"]):
            errors.append(
                f"{uid}: screenshot_review must list indices 1 through {len(available_shots)} "
                "in order with a valid image_kind"
            )
            review = []

        for tag in raw_tags:
            tid = tag.get("tag_id") or tag.get("slot") or ""
            if tag.get("slot") == "presentation":
                unit = tag.get("visual_unit")
                if unit not in visual_policy["controlled_enum"]:
                    errors.append(f"{pname}/{tid}: invalid visual_unit {unit!r}")
                else:
                    visual_units[unit] += 1
                    if genre in ("merge", "match_3") and unit in ("gameplay_board", "main_scene"):
                        missing_halves[genre].add(uid)
                    if preferred_unit is not None and unit != preferred_unit:
                        fallback_reason = tag.get("visual_unit_fallback_reason")
                        if not isinstance(fallback_reason, str) or not fallback_reason.strip():
                            errors.append(f"{pname}/{tid}: missing visual_unit_fallback_reason")
                if not _valid_screenshot_index(tag.get("screenshot_index")):
                    errors.append(f"{pname}/{tid}: screenshot_index must be a positive integer or an array of positive integers")
                visual_region = tag.get("primary_visual_region")
                if not isinstance(visual_region, str) or not visual_region.strip():
                    errors.append(f"{pname}/{tid}: missing primary_visual_region")
                visual_error = _visual_confidence_error(tag, uid, prod.get("confidence"), cfg)
                if visual_error:
                    errors.append(visual_error)
                promo_error = _promo_presentation_error(tag, uid, prod.get("confidence"), review, visual_policy)
                if promo_error:
                    errors.append(promo_error)

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
    _check_l3_supplements(args, totals, missing_halves, errors)

    result = {
        "status": "passed" if not errors else "failed",
        "preset": args.preset,
        "roster_count": len(roster_map),
        "tagged_count": len(tagged_uids),
        "low_confidence_ids": low_conf_ids,
        "visual_unit_distribution": dict(visual_units),
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        result["next_action"] = NEXT_ACTIONS[args.command]
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
    blind_raw = read(Path(args.blind).resolve())
    blind_list = _extract_product_list(blind_raw)
    blind_metadata = blind_raw if isinstance(blind_raw, dict) else {}
    disclosure = blind_metadata.get("protocol_disclosure", proto["disclosure_label"])
    model_record = blind_metadata.get("harness_model_record")
    primary_map = {str(p["unified_app_id"]): p for p in tags_list}

    required_n = int(mode_cfg["blind_sample_size"])
    errors = []
    disclosure_error = _protocol_disclosure_error(disclosure, model_record, proto)
    if disclosure_error:
        errors.append(disclosure_error)
    if len(blind_list) < required_n:
        errors.append(f"blind sample count {len(blind_list)} < required {required_n} for preset '{args.preset}'")

    comparisons = []
    disagreements = []
    axes = tuple(cfg["slots"]["axes"])
    match_counts = dict.fromkeys((*axes, INTENSITY_AXIS, NARRATIVE_AXIS), 0)
    overlaps = []
    colliding_pairs = {axis: defaultdict(int) for axis in match_counts}
    allowed_narratives = cfg[NARRATIVE_AXIS]["controlled_enum"]
    controlled_enum = cfg["visual_unit_policy"]["controlled_enum"]
    visual_units = {"primary": Counter(), "blind": Counter()}
    visual_matches = 0

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
        p_visual = next((tag for tag in p_tags if tag["slot"] == "presentation"), {})
        b_visual = next((tag for tag in b_tags if tag["slot"] == "presentation"), {})
        blind_review = bprod.get("screenshot_review")
        review = [shot for shot in blind_review if isinstance(shot, dict)] if isinstance(blind_review, list) else []
        promo_error = _promo_presentation_error(
            b_visual, uid, bprod.get("confidence"), review, cfg["visual_unit_policy"]
        )
        if promo_error:
            errors.append(f"blind: {promo_error}")
        p_unit = p_visual.get("visual_unit")
        b_unit = b_visual.get("visual_unit")
        visual_match = p_unit == b_unit and p_unit in controlled_enum
        visual_matches += visual_match
        for side, prod, visual in (("primary", pprod, p_visual), ("blind", bprod, b_visual)):
            unit = visual.get("visual_unit")
            if isinstance(unit, str) and unit:
                visual_units[side][unit] += 1
            if unit not in controlled_enum:
                errors.append(f"{side}: {uid}: invalid visual_unit {unit!r}")
            if not _valid_screenshot_index(visual.get("screenshot_index")):
                errors.append(f"{side}: {uid}: screenshot_index must be a positive integer or an array of positive integers")
            visual_error = _visual_confidence_error(visual, uid, prod.get("confidence"), cfg)
            if visual_error:
                errors.append(f"{side}: {visual_error}")
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
            "primary_visual_unit": p_unit,
            "blind_visual_unit": b_unit,
            "visual_unit_match": visual_match,
            "primary_screenshot_index": p_visual.get("screenshot_index"),
            "blind_screenshot_index": b_visual.get("screenshot_index"),
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
        if not visual_match or not all(axis_matches.values()) or overlap < 1.0:
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
        "protocol_disclosure": disclosure,
        "sample_evaluated": n_eval,
        "sample_required": required_n,
        "theme_primary_agreement": round(theme_rate, 4),
        "presentation_agreement": round(agreement["presentation"], 4),
        "visual_unit_agreement": round(visual_matches / n_eval, 4) if n_eval else 0.0,
        "visual_unit_distribution": {side: dict(counts) for side, counts in visual_units.items()},
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
    if isinstance(model_record, dict) and model_record:
        report["harness_model_record"] = model_record
    if not passed_thresholds:
        report["next_action"] = NEXT_ACTIONS[args.command]
    out_path = Path(args.output).resolve()
    write(out_path, report)
    result = {
        "status": "passed" if passed_thresholds else "failed",
        "output": str(out_path),
        "theme_primary_agreement": report["theme_primary_agreement"],
        "presentation_agreement": report["presentation_agreement"],
        "visual_unit_agreement": report["visual_unit_agreement"],
        "visual_unit_distribution": report["visual_unit_distribution"],
        "fusion_agreement": report["fusion_agreement"],
        "narrative_intensity_agreement": report["narrative_intensity_agreement"],
        "narrative_motif_agreement": report["narrative_motif_agreement"],
        "narrative_agreement": report["narrative_agreement"],
        "colliding_pairs_by_axis": report["colliding_pairs_by_axis"],
        "mean_tag_set_overlap": report["mean_tag_set_overlap"],
        "disagreement_count": len(disagreements),
        "low_confidence_count": len(low_conf),
        "errors": errors,
    }
    if not passed_thresholds:
        result["next_action"] = report["next_action"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed_thresholds else 2


def _apply_narrative_adjudication(prod, decision, cfg):
    explicit_fields = set()
    for field in (INTENSITY_AXIS, NARRATIVE_AXIS, "narrative_evidence"):
        key = next((key for key in (field, f"final_{field}") if key in decision), None)
        if key is not None:
            prod[field] = decision[key]
            explicit_fields.add(field)
            if field == NARRATIVE_AXIS:
                prod["tags"] = _main_tags(prod)
    check_intensity = INTENSITY_AXIS not in explicit_fields
    check_motif = NARRATIVE_AXIS not in explicit_fields
    if not (check_intensity or check_motif):
        return

    uid = prod["unified_app_id"]
    allowed_motifs = cfg[NARRATIVE_AXIS]["controlled_enum"]
    current_motif = _checked_narrative(prod, allowed_motifs) if check_motif else None
    intensity_values = "|".join(re.escape(value) for value in cfg[INTENSITY_AXIS]["controlled_enum"])
    motif_values = "|".join(re.escape(value) for value in sorted(allowed_motifs, key=len, reverse=True))
    change_intensity = re.compile(
        r"(?<!不)(?<!未)(?<!无需)(?<!不足以)(?:改|调整|[降升][级格]|[降升]为|定为)"
        rf"[为成至到]?[\s：:「『“]*({intensity_values})"
    )
    change_motif = re.compile(r"母题\s*(?:(?:从|由)[^，。；\n]*?)?(?:改|调整)[为成至到]?|母题\s*清空")
    for field in ("note", "decision"):
        text = str(decision.get(field) or "").strip()
        if check_intensity:
            new_intensities = change_intensity.findall(text)
            if re.fullmatch(intensity_values, text):
                new_intensities.append(text)
            if any(value != prod.get(INTENSITY_AXIS) for value in new_intensities):
                raise ValueError(f"{uid}: {field} changes narrative_intensity without an explicit narrative_intensity field")
        if not check_motif:
            continue
        for change in change_motif.finditer(text):
            target = re.match(rf"[\s：:「『“]*({motif_values}|清空|留空|空|null|None)", text[change.end():])
            if change.group().endswith("清空"):
                new_motif = None
            elif target is None:
                raise ValueError(f"{uid}: {field} changes narrative_motif without an explicit narrative_motif field")
            else:
                new_motif = target[1] if target[1] in allowed_motifs else None
            if new_motif != current_motif:
                raise ValueError(f"{uid}: {field} changes narrative_motif without an explicit narrative_motif field")


def _check_final_exclusions(prod, tags):
    exclusions = prod.get("excluded_near_tags")
    if not isinstance(exclusions, list):
        return
    for tag in tags:
        tid = tag.get("tag_id")
        if not tid:
            continue
        pattern = re.compile(rf"\b{re.escape(tid)}\b", re.ASCII | re.IGNORECASE)
        for index, entry in enumerate(exclusions):
            if pattern.search(str(entry)):
                raise ValueError(
                    f"{prod['unified_app_id']}: excluded_near_tags[{index}] references final tag_id {tid!r}"
                )


def _check_final_emerging(entries, hits_by_id, preset, roster_path, policy):
    roster, window = {}, {}
    if roster_path:
        roster_doc = read(Path(roster_path).resolve())
        roster = {str(row["unified_app_id"]): row for row in _extract_product_list(roster_doc)}
        if isinstance(roster_doc, dict):
            window = roster_doc.get("new_release_window") or {}

    for entry in entries:
        if entry.get("emerging") is not True:
            continue
        tid = entry["tag_id"]
        hits = hits_by_id.get(tid, set())
        if preset == "full" and len(hits) >= policy["revert_to_false_on_full_hits_min"]:
            raise ValueError(f"{tid}: emerging must be false in full after {len(hits)} final hits")
        if len(hits) != 1:
            raise ValueError(f"{tid}: emerging=true requires exactly one final hit, got {len(hits)}")
        if not roster_path:
            continue
        uid = next(iter(hits))
        app = roster.get(uid, {})
        if app.get("is_new_release") is not True:
            raise ValueError(f"{tid}: emerging hit {uid} must be a verified new release in --roster")
        try:
            start = date.fromisoformat(window.get("start"))
            end = date.fromisoformat(window.get("end"))
            launch = date.fromisoformat(app.get("effective_launch_date"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{tid}: emerging hit {uid} requires an effective_launch_date and new_release_window dates"
            ) from exc
        if not start <= launch <= end:
            raise ValueError(f"{tid}: emerging hit {uid} must be within new_release_window")


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

    disclosure_error = _protocol_disclosure_error(
        qc["protocol_disclosure"], qc.get("harness_model_record"), cfg["blind_retest_protocol"],
    )
    if disclosure_error:
        raise ValueError(disclosure_error)
    if not qc.get("passed"):
        raise ValueError("cannot finalize tagged_corpus before blind QC passes its thresholds")

    sources = None
    if args.bundle:
        bundle = read(Path(args.bundle).resolve())
        sources = bundle.get("sources") if isinstance(bundle, dict) else None
        if not isinstance(sources, dict):
            raise ValueError("tagging input bundle must contain a sources object")

    adjudications = {}
    adj_metadata = {}
    if args.adjudications:
        adj_path = Path(args.adjudications).resolve()
        if adj_path.is_file():
            adj_raw = read(adj_path)
            adj_metadata = adj_raw if isinstance(adj_raw, dict) else {}
            if adj_metadata.get("status") == "pending_human_adjudication":
                raise ValueError("adjudications status is pending_human_adjudication")
            adj_items = adj_metadata.get("decisions", adj_raw)
            if isinstance(adj_items, list):
                for item in adj_items:
                    uid = str(item.get("unified_app_id") or "") if isinstance(item, dict) else ""
                    if uid:
                        adjudications[uid] = item
            elif isinstance(adj_items, dict):
                metadata_keys = {"adjudicated_by", "adjudicated_by_human", "adjudicator_role", "status", "pending"}
                adjudications = {
                    str(k): v for k, v in adj_items.items()
                    if adj_items is not adj_raw or k not in metadata_keys
                }
            if "pending" in adj_metadata and not any(
                isinstance(adjudications.get(uid), dict) and adjudications[uid]
                for uid in (str(prod["unified_app_id"]) for prod in products)
            ):
                raise ValueError("adjudications contains pending entries without valid decisions")

    required_matches = (
        "theme_match", "presentation_match", "fusion_match", "narrative_intensity_match",
        "narrative_match", "visual_unit_match",
    )
    unresolved_diffs = [
        d["unified_app_id"] for d in qc.get("disagreements", [])
        if any(d.get(key) is False for key in required_matches)
        and not (isinstance(adjudications.get(str(d["unified_app_id"])), dict) and adjudications[str(d["unified_app_id"])])
    ]
    if unresolved_diffs:
        raise ValueError(f"unresolved blind disagreements require explicit adjudication: {unresolved_diffs}")

    applied_log = []
    unresolved_low = []
    hits_by_id = defaultdict(set)
    axes = cfg["slots"]["axes"]
    for prod in products:
        uid = str(prod["unified_app_id"])
        original_confidence = prod.get("confidence")
        if uid in adjudications:
            dec = adjudications[uid]
            if not isinstance(dec, dict):
                raise ValueError(f"{uid}: adjudication must be an object")
            if dec.get("status") == "pending_human_adjudication":
                raise ValueError(f"{uid}: adjudication status is pending_human_adjudication")
            dec_by = str(dec.get("adjudicated_by") or adj_metadata.get("adjudicated_by") or "human")
            if isinstance(dec.get("final_tags"), list) and dec["final_tags"]:
                for t in dec["final_tags"]:
                    if t.get("tag_id") not in vocab_map:
                        raise ValueError(f"adjudication uses unknown tag_id: {t.get('tag_id')}")
                prod["tags"] = dec["final_tags"]
            for field in ("excluded_near_tags", "final_excluded_near_tags"):
                if field in dec:
                    exclusions = dec[field]
                    if not isinstance(exclusions, list) or not exclusions:
                        raise ValueError(f"{uid}: adjudication {field} must be a nonempty array")
                    prod["excluded_near_tags"] = exclusions
            _apply_narrative_adjudication(prod, dec, cfg)
            intensity = _checked_narrative_intensity(prod, cfg)
            prod["adjudicated_by"] = dec_by
            is_human = dec.get("adjudicated_by_human")
            if not isinstance(is_human, bool):
                is_human = adj_metadata.get("adjudicated_by_human")
            if not isinstance(is_human, bool):
                role = dec.get("adjudicator_role") or adj_metadata.get("adjudicator_role")
                is_human = role == "human" if role else dec_by.strip().lower() == "human"
            prod["adjudicated_by_human"] = is_human
            default_note = "人工复核确认" if prod["adjudicated_by_human"] else "复核确认"
            prod["adjudication_note"] = str(dec.get("note") or dec.get("decision") or default_note)
            if original_confidence == "low":
                prod["confidence"] = "adjudicated"
            if sources is not None:
                evidence_errors = _narrative_evidence_errors(prod, intensity, sources, cfg)
                if evidence_errors:
                    raise ValueError("；".join(evidence_errors))
            applied_log.append({
                "unified_app_id": uid, "app_name": prod.get("app_name"), "note": prod["adjudication_note"],
                "adjudicated_by": dec_by, "adjudicated_by_human": is_human,
            })
        if prod.get("confidence") == "low":
            unresolved_low.append(uid)
        prod[NARRATIVE_AXIS] = _checked_narrative(prod, cfg[NARRATIVE_AXIS]["controlled_enum"])
        prod["tags"] = _main_tags(prod)
        final_tags = [tag for tag in prod["tags"] if tag.get("slot") in axes]
        _check_final_exclusions(prod, final_tags)
        copy_errors = _narrative_copy_errors(prod, final_tags)
        if copy_errors:
            raise ValueError("；".join(copy_errors))
        for tag in final_tags:
            hits_by_id[tag.get("tag_id")].add(uid)
        review = prod.get("screenshot_review")
        for tag in prod["tags"]:
            if tag.get("slot") == "presentation":
                visual_error = _visual_confidence_error(tag, uid, original_confidence, cfg)
                if visual_error:
                    raise ValueError(visual_error)
                if review is not None:
                    promo_error = _promo_presentation_error(tag, uid, original_confidence, review, cfg["visual_unit_policy"])
                    if promo_error:
                        raise ValueError(promo_error)

    if len(unresolved_low) > max_unresolved_low:
        raise ValueError(f"unresolved low_confidence products remain ({len(unresolved_low)}): {unresolved_low}")
    _check_final_emerging(vocab_entries, hits_by_id, preset, args.roster, cfg["emerging_policy"])

    vocab_metadata = vocab_raw if isinstance(vocab_raw, dict) else {}
    vocab_approval = {
        "approved_by": vocab_metadata.get("approved_by", "human"),
        "approved_date": vocab_metadata.get("approved_date", ""),
        "signoff_note": vocab_metadata.get("signoff_note", ""),
    }
    if "script_gate_receipt" in vocab_metadata:
        vocab_approval["script_gate_receipt"] = vocab_metadata["script_gate_receipt"]

    corpus = {
        "schema_version": "1.0.0",
        "status": "verified",
        "preset": preset,
        "finalized_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vocab_approval": vocab_approval,
        "vocab": vocab_entries,
        "products": products,
        "qc_summary": {
            "protocol_disclosure": qc["protocol_disclosure"],
            "sample_evaluated": qc["sample_evaluated"],
            "theme_primary_agreement": qc["theme_primary_agreement"],
            "presentation_agreement": qc.get("presentation_agreement"),
            "visual_unit_agreement": qc.get("visual_unit_agreement"),
            "visual_unit_distribution": qc.get("visual_unit_distribution"),
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
    p_vv.add_argument("--roster", help="Frozen product roster JSON")
    p_vv.add_argument("--pass1", help="Pass 1 screenshot review JSON")
    p_vv.add_argument("--gaps", help="Supplement evidence register JSON (defaults to gap_register.json beside --roster)")
    p_vv.add_argument("--draft-md", default=None, help="Deprecated compatibility option")
    p_vv.set_defaults(func=cmd_validate_vocab)

    p_rs = sub.add_parser("render-signoff", help="Validate vocabulary and render the signoff page and summary")
    p_rs.add_argument("--vocab", required=True)
    p_rs.add_argument("--roster", required=True)
    p_rs.add_argument("--pass1", required=True)
    p_rs.add_argument("--gaps", help="Supplement evidence register JSON (defaults to gap_register.json beside --roster)")
    p_rs.add_argument("--preset", choices=("pilot", "full"), required=True)
    p_rs.add_argument("--output")
    p_rs.add_argument("--prev-vocab")
    p_rs.add_argument("--summary-md")
    p_rs.add_argument("--prior-vocab")
    p_rs.set_defaults(func=cmd_render_signoff)

    p_vt = sub.add_parser("validate-tags", help="Validate Pass 2 tags against slots, vocab and non-marketing evidence rules")
    p_vt.add_argument("--vocab", required=True)
    p_vt.add_argument("--tags", required=True)
    p_vt.add_argument("--roster", required=True)
    p_vt.add_argument("--pass1", help="Pass 1 screenshot review JSON")
    p_vt.add_argument("--gaps", help="Supplement evidence register JSON (defaults to gap_register.json beside --roster)")
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
    p_fn.add_argument("--bundle", default=None, help="Tagging input bundle for adjudicated narrative evidence checks")
    p_fn.add_argument("--roster", help="Frozen roster for final emerging validation")
    p_fn.add_argument("--output", required=True)
    p_fn.set_defaults(func=cmd_finalize)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"status": "failed", "reason": str(exc)}
        if args.command in NEXT_ACTIONS:
            result.update(errors=[str(exc)], next_action=NEXT_ACTIONS[args.command])
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

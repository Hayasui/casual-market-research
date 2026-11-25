"""Explicit HTML assembly and offline delivery; no research-run state is read.

Adapted from game-market-competitor-report v0.5.3 with ONLY two surgical extensions:
1. Preset-gated inline script whitelist (zero_js by default; whitelisted_inline_js allows <=150-line
   dependency-free script with data-approved-inline="dashboard-ui").
2. Desensitized [[sources]] source-card rendering per configs/workspace.json.
Original dual-HTML generation, [[metric]]/[[cite]]/[[chart]]/[[image]] binding, atomic publish and validate are preserved.
"""
import base64
from copy import deepcopy
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.etree import ElementTree

from common import digest, ident, read
from report_values import display
from report_view import E, STYLE
from tool_results import payload_state

TOKEN = re.compile(r"\[\[(style|sources|metric|cite|chart|image)(?:\s+([^\n]*?))?\]\]")
PREVIEW_STYLESHEET = '<link rel="stylesheet" href="report.css" data-template-preview>'
LEGACY_OUTPUTS = ("report.html", "report.base64.html")
FORBIDDEN_JS_RE = re.compile(
    r"\beval\s*\(|\bnew\s+Function\b|\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b|\bdocument\.write\b|\bimportScripts\b|https?://",
    re.I,
)
DEFAULT_SANITIZATION = {
    "allowed_source_card_fields": ["params", "observation_scope", "url", "retrieved_at", "raw_file"],
    "strip_param_keys": ["c", "a", "token_sign", "_source", "mcp_trace_id", "mcp_user_question"],
    "provider_display_map": {
        "Sensor Tower": "Sensor Tower",
        "yilan_store_metadata": "应用商店公开元数据",
        "yilan_ad_creatives": "第三方投放监测素材",
        "yilan_new_apps": "第三方新游监测索引",
        "app_store_public": "应用商店公开页",
        "web_film_plot": "公开维基与影评资料",
        "web_public": "公开网页资料",
    },
}


def _load_sanitization_policy():
    cfg_path = Path(__file__).resolve().parents[1] / "configs/workspace.json"
    if cfg_path.is_file():
        try:
            pol = read(cfg_path).get("sanitization_policy")
            if isinstance(pol, dict):
                return {**DEFAULT_SANITIZATION, **pol}
        except (OSError, ValueError):
            pass
    return DEFAULT_SANITIZATION


def _sanitize_card_details(item, policy):
    allowed = [k for k in policy.get("allowed_source_card_fields", []) if k != "provider"]
    strip_params = set(policy.get("strip_param_keys", []))
    details = {}
    for key in allowed:
        if key not in item:
            continue
        val = deepcopy(item[key])
        if key == "params" and isinstance(val, dict):
            val = {k: v for k, v in val.items() if k not in strip_params and not str(k).startswith("_internal")}
        elif key == "raw_file" and isinstance(val, str):
            rf = val.replace("\\", "/")
            if "research/" in rf:
                rf = "research/" + rf.split("research/", 1)[1]
            elif rf.startswith("/") or re.match(r"^[A-Za-z]:/", rf):
                rf = Path(rf).name
            val = rf
        elif key == "url" and isinstance(val, str) and "OSSAccessKeyId=" in val:
            parts = urlsplit(val)
            val = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        details[key] = val
    return details


class TemplateStructure(HTMLParser):
    def __init__(self, source, *, script_mode="zero_js"):
        super().__init__()
        if script_mode not in {"zero_js", "whitelisted_inline_js"}:
            raise ValueError("unsupported script_mode: " + str(script_mode))
        self.script_mode = script_mode
        self.ids, self.cases = set(), {}
        self.missing = False
        self.placeholders = False
        self.images, self.tokens, self.anchors = [], [], []
        self._heading_text = {}
        self._heading = None
        self._in_head = False
        self._in_script = False
        self._script_buf = []
        self.script_count = 0
        self.feed(source)
        self.close()

    @property
    def report_title(self):
        for tag in ("h1", "title"):
            title = " ".join("".join(self._heading_text.get(tag, [])).split())
            if title:
                return title
        return ""

    def handle_data(self, data):
        if self._in_script:
            self._script_buf.append(data)
            return
        self.tokens.extend(m[0] for m in TOKEN.finditer(data))
        if self._heading:
            self._heading_text[self._heading].append(data)

    def handle_startendtag(self, tag, pairs):
        self.handle_starttag(tag, pairs)
        if tag == "script" and self._in_script:
            self.handle_endtag("script")

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag == "head":
            self._in_head = True
        if (tag == "h1" or (tag == "title" and self._in_head)) and tag not in self._heading_text:
            if attrs.get("data-placeholder") != "true":
                self._heading = tag
                self._heading_text[tag] = []
        if tag == "br" and self._heading:
            self._heading_text[self._heading].append(" ")
        if (attrs.get("data-placeholder") == "true" or attrs.get("data-product") == "replace_product"
                or "replace_product" in (attrs.get("id") or "").split("-")):
            self.placeholders = True
        if tag == "script":
            if self.script_mode != "whitelisted_inline_js":
                raise ValueError("unsupported offline template element: script")
            if self.script_count >= 1:
                raise ValueError("whitelisted_inline_js allows at most one inline script block")
            if attrs.get("data-approved-inline") != "dashboard-ui" or "src" in attrs:
                raise ValueError("script requires data-approved-inline='dashboard-ui' without external src")
            self.script_count += 1
            self._in_script = True
            self._script_buf = []
        elif tag in {"iframe", "object", "embed", "base", "link", "video", "audio", "source"}:
            raise ValueError("unsupported offline template element: " + tag)
        for key, value in pairs:
            if key.startswith("on") or (value and "[[" in value):
                raise ValueError("place template tokens in HTML text, not attributes")
            if key in {"srcset", "poster", "background"} or (key == "src" and tag != "img"):
                raise ValueError("offline resources must use chart/image tokens")
            if key in {"href", "xlink:href"} and tag in {"use", "image"} and value and not value.startswith("#"):
                raise ValueError("external SVG resources are unsupported")
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            raise ValueError("offline template cannot redirect")
        if tag == "img":
            self.images.append(attrs.get("src"))
        if attrs.get("id"):
            if attrs["id"] in self.ids:
                raise ValueError("duplicate HTML id: " + attrs["id"])
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href", "").startswith("#"):
            self.anchors.append(attrs["href"][1:])
        if attrs.get("data-product") and attrs.get("data-module"):
            self.cases.setdefault(attrs["data-product"], set()).add(attrs["data-module"])
        if attrs.get("data-status") == "missing" or "missing" in (attrs.get("class") or "").split():
            self.missing = True

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            body = "".join(self._script_buf)
            lines = [ln for ln in body.splitlines() if ln.strip()]
            if len(lines) > 150:
                raise ValueError(f"whitelisted inline script exceeds 150 lines ({len(lines)})")
            if "[[" in body:
                raise ValueError("template tokens are forbidden inside inline script")
            if FORBIDDEN_JS_RE.search(body):
                raise ValueError("inline script contains forbidden dynamic or network API")
            self._in_script = False
            self._script_buf = []
        if tag == self._heading:
            self._heading = None
        if tag == "head":
            self._in_head = False


def _output_names(title):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip(" .-")[:100].rstrip(" .") or "report"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]|CONIN\$|CONOUT\$",
                    stem.split(".", 1)[0].rstrip(), re.I):
        stem = "_" + stem
    if stem.lower().endswith(".base64"):
        stem += "_"
    return f"{stem}.html", f"{stem}.base64.html"


def _load_inputs(inputs, base):
    """Resolve only files explicitly named by this call."""
    data = deepcopy(inputs)
    paths = set()

    def load(value):
        if isinstance(value, str):
            path = (base / value).resolve()
            paths.add(path)
            return read(path)
        if not isinstance(value, dict):
            raise ValueError("calculation results and sources must be JSON objects or paths")
        return value

    data["data"] = load(data.get("data", {}))
    data["normalized"] = load(data.get("normalized", {}))
    data["results"] = {key: load(value) for key, value in data.get("results", {}).items()}
    sources = {}

    def merge(items):
        if not isinstance(items, dict):
            raise ValueError("sources must map IDs to provenance")
        for key, value in items.items():
            if key in sources and sources[key] != value:
                raise ValueError("conflicting source definitions: " + key)
            sources[key] = value

    merge(load(data.get("sources", {})))
    for result in [data["data"], data["normalized"], *data["results"].values()]:
        merge(result.get("sources", {}))
    data["sources"] = sources
    return data, paths


def _svg_check(raw):
    if re.search(br"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise ValueError("SVG cannot depend on external document definitions")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid local SVG") from exc
    if root.tag.split("}")[-1].lower() != "svg":
        raise ValueError("SVG MIME requires an SVG document")
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise ValueError("SVG must be static")
        for key, value in node.attrib.items():
            key = key.split("}")[-1].lower()
            if key.startswith("on") or (key in {"href", "src"} and not value.startswith("#")):
                raise ValueError("SVG contains an external or active resource")
        for value in [node.text or "", *node.attrib.values()]:
            urls = re.findall(r"url\s*\(([^)]+)\)", value, re.I)
            if re.search(r"@import\b", value, re.I) or any(
                not url.strip(" \t\r\n\"'").startswith("#") for url in urls
            ):
                raise ValueError("SVG stylesheet contains external resources")


def assemble(source, inputs, *, base_dir=".", script_mode="zero_js"):
    """Return both HTML strings and their actual image bytes, without writing files."""
    base = Path(base_dir).resolve()
    if not re.match(r"\s*<!doctype html>", source, re.I) or not re.search(r"</html>\s*$", source, re.I):
        raise ValueError("render requires a complete HTML template")
    source = source.replace(PREVIEW_STYLESHEET, "<style>[[style]]</style>", 1)
    structure = TemplateStructure(source, script_mode=script_mode)
    if structure.tokens != [m[0] for m in TOKEN.finditer(source)]:
        raise ValueError("place template tokens in HTML text, not comments or attributes")
    if structure.images or re.search(r"@import\b|url\s*\(", source, re.I):
        raise ValueError("use chart/image tokens for offline resources and inline CSS")
    if source.count("[[sources]]") != 1:
        raise ValueError("template requires exactly one [[sources]] token")
    data, paths = _load_inputs(inputs, base)
    structure.input_paths = paths
    structure.result_hashes = {"data": digest(data["data"]), "normalized": digest(data["normalized"]),
                               "results": {name: digest(value) for name, value in data["results"].items()}}
    structure.sources_hash = digest(data["sources"])
    sources = data["sources"]
    numbers = {eid: n for n, eid in enumerate(sources, 1)}
    cited, used = set(), {}
    bound = 0
    chart_num = 0

    def citations(ids):
        if not isinstance(ids, list) or not ids:
            raise ValueError("reference requires explicit source IDs")
        for eid in ids:
            ident(eid)
            if eid not in sources or not isinstance(sources[eid], dict):
                raise ValueError("unknown source reference: " + eid)
            item = sources[eid]
            if not item.get("provider"):
                raise ValueError("source requires an actual provider: " + eid)
            if item.get("status", "ok") not in {"ok", "available", "accepted"}:
                raise ValueError(item.get("error") or "unusable source: " + eid)
            if any(payload_state(value)["status"] == "incomplete" for value in (item, item.get("response", {}))):
                raise ValueError("truncated evidence cannot be cited as complete: " + eid)
        cited.update(ids)
        return " ".join(f'<a class="source-ref" href="#src-{E(e)}">[{numbers[e]:02d}]</a>' for e in ids)

    def expand(match):
        nonlocal bound, chart_num
        kind, arg = match.groups()
        if kind == "style" and arg is None:
            return STYLE
        if kind == "sources" and arg is None:
            return match[0]
        if kind == "metric":
            reference = json.loads(arg)
            result_name = reference.pop("result", None)
            if result_name is not None and result_name not in data["results"]:
                raise ValueError("unknown calculation result: " + str(result_name))
            result = data["results"][result_name] if result_name is not None else data["data"]
            value, row = display(reference, data["normalized"], result)
            refs = row.get("evidence_ids", [])
            citation = citations(refs) if refs else ""
            if row["value"] is not None and not refs:
                raise ValueError("numeric values require source references")
            if any(sources[e]["provider"] != "Sensor Tower" for e in refs):
                raise ValueError("market and store-review numeric sources must be Sensor Tower")
            bound += 1
            if row["value"] is None:
                structure.missing = True
            notes = "；".join(row.get("scope_limitations", []))
            return f'<span class="metric-value" title="{E(notes)}">{E(value)}</span> ' + citation
        if kind == "cite":
            return citations([arg])
        if kind not in {"image", "chart"} or not arg:
            raise ValueError("invalid template token")
        parts = arg.split()
        aid = ident(parts[0])
        record = deepcopy(data.get("charts" if kind == "chart" else "assets", {}).get(aid))
        if not isinstance(record, dict):
            raise ValueError("unknown local asset: " + aid)
        metadata_path = None
        if record.get("metadata"):
            metadata_path = (base / record["metadata"]).resolve()
            structure.input_paths.add(metadata_path)
            record = {**read(metadata_path), **record}
        if record.get("status", "available") not in {"available", "accepted"}:
            raise ValueError("unavailable report asset: " + aid)
        expected = record.get("product_id")
        if parts[1:] != ([expected] if expected else []):
            raise ValueError("asset product does not match: " + aid)
        if kind == "chart":
            result_name = record.get("result")
            if result_name is not None and result_name not in data["results"]:
                raise ValueError("unknown chart calculation result: " + str(result_name))
            result = data["results"][result_name] if result_name is not None else data["data"]
            expected_hash = record.get("data_hash", record.get("metrics_hash"))
            if expected_hash is not None and expected_hash != digest(result):
                raise ValueError("chart data changed; rebuild this chart: " + aid)
        original = base / record["file"]
        supplied = data.get("charts" if kind == "chart" else "assets", {})[aid]
        if metadata_path and "file" not in supplied:
            original = metadata_path.parent / record["file"]
        original = original.resolve()
        structure.input_paths.add(original)
        raw = original.read_bytes()
        if record.get("sha256") is not None and digest(raw) != record["sha256"]:
            raise ValueError("asset bytes changed: " + aid)
        mime = record["mime"]
        if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}:
            raise ValueError("unsupported local image MIME")
        if mime == "image/svg+xml":
            _svg_check(raw)
        else:
            signatures = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",),
                          "image/gif": (b"GIF87a", b"GIF89a"), "image/webp": (b"RIFF",)}
            if not raw.startswith(signatures[mime]) or (mime == "image/webp" and raw[8:12] != b"WEBP"):
                raise ValueError("local image bytes do not match MIME: " + aid)
        name = "image/" + original.name
        item = (mime, raw)
        if name in used and used[name] != item:
            raise ValueError("colliding report asset filename: " + name)
        used[name] = item
        caption = record.get("caption", "")
        title = str(record.get("title") or "")
        if kind == "chart":
            chart_num += 1
            if not re.match(r"^图\s*\d+", title):
                title = f"图 {chart_num}　{title}"
        role = {"icon": "case-icon", "screenshot": "case-screenshot"}.get(record.get("role"), "")
        image_src = E(quote(name, safe="/"))
        image_label = E(title or caption)
        if role == "case-icon":
            citations(record["evidence_ids"])
            return f'<figure class="case-icon"><img src="{image_src}" alt="{image_label}" title="{image_label}"></figure>'
        heading = f'<div class="figure-title">{E(title)}</div>' if title else ""
        events = "".join(f'<li><strong>{E(e["month"])}</strong> {E(e["text"])} {citations(e["evidence_ids"])}</li>'
                         for e in record.get("events", []))
        event_list = f'<ol class="chart-events">{events}</ol>' if events else ""
        return (f'<figure class="{role}">{heading}<img src="{image_src}" alt="{image_label}">'
                f'<figcaption>{E(caption)} {citations(record["evidence_ids"])}</figcaption>{event_list}</figure>')

    external = TOKEN.sub(expand, source)
    if "[[" in external.replace("[[sources]]", ""):
        raise ValueError("unrecognized or unresolved template token")

    policy = _load_sanitization_policy()
    prov_map = policy.get("provider_display_map", {})
    source_html = []
    for eid, item in sources.items():
        if eid not in cited:
            continue
        disp_provider = prov_map.get(item["provider"], item["provider"])
        details = _sanitize_card_details(item, policy)
        excerpt = str(item.get("text", ""))[:800].replace("[[", "&#91;&#91;")
        source_html.append(
            f'<article id="src-{E(eid)}" class="source-card"><h3>[{numbers[eid]:02d}] '
            f'{E(disp_provider)} · {E(eid)}</h3><details><summary>来源参数与摘录</summary>'
            f'<pre>{E(json.dumps(details, ensure_ascii=False))}</pre>'
            + (f'<blockquote>{E(excerpt)}</blockquote>' if excerpt else '')
            + '</details></article>'
        )
    external = external.replace("[[sources]]", "".join(source_html) or '<p class="note">暂无引用来源。</p>')
    final = TemplateStructure(external, script_mode=script_mode)
    if set(final.anchors) - final.ids:
        raise ValueError("HTML contains unresolved internal links")
    structure.report_outputs = _output_names(final.report_title)

    def embed(match):
        mime, raw = used[unquote(unescape(match[1]))]
        return f'<img src="data:{mime};base64,{base64.b64encode(raw).decode()}"'

    embedded = re.sub(r'<img src="([^"]+)"', embed, external)
    return external, embedded, used, structure, bound


def _prepare(payload, base_dir, *, script_mode="zero_js"):
    base = Path(base_dir).resolve()
    template = (base / payload["template"]).resolve()
    source = template.read_text(encoding="utf-8-sig")
    external, embedded, assets, structure, bound = assemble(source, payload, base_dir=base, script_mode=script_mode)
    html_names = structure.report_outputs
    files = dict(zip(html_names, (external.encode("utf-8"), embedded.encode("utf-8"))))
    files.update({name: raw for name, (_, raw) in assets.items()})
    protected = structure.input_paths | {template}
    metadata = {
        "status": "assembled",
        "script_mode": script_mode,
        "template_hash": digest(source),
        "input_hash": digest({"request": payload, "template": source, "results": structure.result_hashes,
                              "sources": structure.sources_hash, "script_mode": script_mode,
                              "assets": {name: digest(raw) for name, (_, raw) in assets.items()}}),
        "result_hashes": structure.result_hashes,
        "bound_metrics": bound,
        "marked_missing": structure.missing,
        "cited_sources": len(re.findall(r'<article id="src-', external)),
        "asset_hashes": {name: digest(raw) for name, (_, raw) in assets.items()},
        "browser_render_verified": False,
        "warnings": (["no_metric_references"] if bound == 0 else [])
        + (["template_placeholders_remaining"] if structure.placeholders else []),
    }
    return html_names, files, metadata, protected


def _publish(output, files):
    """Prepare every file before publishing; restore earlier bytes on write failure."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    touched, backups = [], {}
    with tempfile.TemporaryDirectory(prefix=".html-build-", dir=output.parent) as folder:
        stage = Path(folder)
        for name, raw in files.items():
            target = output / name
            if target.is_symlink() or not target.resolve().is_relative_to(output):
                raise ValueError("delivery target leaves the output directory: " + name)
            if target.exists() and not target.is_file():
                raise ValueError("output conflicts with an existing directory: " + name)
            staged = stage / "new" / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(raw)
            if target.exists():
                backup = stage / "old" / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(target.read_bytes())
                backups[name] = backup
        try:
            for name in files:
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / "new" / name, target)
                touched.append(name)
        except OSError:
            for name in reversed(touched):
                if name in backups:
                    os.replace(backups[name], output / name)
                else:
                    (output / name).unlink()
            raise


def render(output_dir, inputs, *, base_dir=".", metadata_path=None, manifest_path=None, script_mode="zero_js"):
    """Write the two HTML files and referenced images from explicit inputs."""
    html_names, files, metadata, protected = _prepare(inputs, base_dir, script_mode=script_mode)
    if manifest_path is not None:
        protected.add(Path(manifest_path).resolve())
    output = Path(output_dir).resolve()
    for name, raw in files.items():
        target = (output / name).resolve()
        if target in protected and not (name.startswith("image/") and target.read_bytes() == raw):
            raise ValueError("delivery must not overwrite its explicit inputs")
    metadata.update(status="written", deliverables=[str(output / name) for name in html_names]
                    + ([str(output / "image")] if metadata["asset_hashes"] else []),
                    files={name: digest(raw) for name, raw in files.items()})
    if metadata_path:
        path = Path(metadata_path).resolve()
        if not path.is_relative_to(output) or path in protected:
            raise ValueError("HTML metadata must be an output file inside the output directory")
        name = path.relative_to(output).as_posix()
        if any(path == (output / delivery_name).resolve() for delivery_name in files):
            raise ValueError("HTML metadata conflicts with a delivery file")
        files[name] = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    _publish(output, files)
    return metadata


def validate(output_dir, inputs, *, base_dir=".", script_mode="zero_js"):
    """Check this delivery against only the explicitly supplied inputs."""
    output = Path(output_dir).resolve()
    html_names, files, metadata, _ = _prepare(inputs, base_dir, script_mode=script_mode)
    if (html_names != LEGACY_OUTPUTS
            and not any((output / name).exists() for name in html_names)
            and all((output / name).is_file() for name in LEGACY_OUTPUTS)):
        html_bytes = [files.pop(name) for name in html_names]
        html_names = LEGACY_OUTPUTS
        files.update(zip(html_names, html_bytes))
    errors = []
    for name, raw in files.items():
        path = output / name
        if not path.is_file() or path.read_bytes() != raw:
            errors.append("delivery content differs: " + name)
    metadata.update(status="checked" if not errors else "failed", errors=errors,
                    structural_valid=not errors, deliverables=[str(output / name) for name in html_names]
                    + ([str(output / "image")] if metadata["asset_hashes"] else []))
    return metadata

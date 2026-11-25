"""Explicit-input atomic tools: market calculation, SVG chart, dual HTML delivery and validation.

Adapted from game-market-competitor-report v0.5.3:
- Retains market, chart, html, validate (removes unused reviews subcommand).
- Supports relative JSON path for chart.data so Stage 1 chart specs resolve market_result.json cleanly.
- Forwards --script-mode to html_delivery (zero_js default for pilot; whitelisted_inline_js for full).
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import digest, read, write
from tool_results import payload_state


def check_sources(sources, references, *, require_st=True):
    if not isinstance(sources, dict):
        raise ValueError("sources must map source IDs to actual provenance")
    for ref in references:
        if ref not in sources:
            raise ValueError("unknown source reference: " + str(ref))
        source = sources[ref]
        if not isinstance(source, dict) or not source.get("provider"):
            raise ValueError("sources require explicit providers")
        if require_st and source["provider"] != "Sensor Tower":
            raise ValueError("market numeric sources must be Sensor Tower")
        if source.get("status", "ok") not in {"ok", "available"} or any(
            payload_state(item)["status"] == "incomplete" for item in (source, source.get("response", {}))
        ):
            raise ValueError("failed or truncated source cannot supply complete input: " + ref)


def references(rows):
    result = []
    for row in rows:
        refs = row.get("evidence_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or not r for r in refs):
            raise ValueError("each input requires nonempty evidence_ids")
        result.extend(refs)
    return list(dict.fromkeys(result))


def run_tool(command, payload, *, base_dir="."):
    """Pure input/output boundary: resolves only explicitly supplied paths."""
    data = deepcopy(payload)
    base = Path(base_dir).resolve()
    if command == "chart" and isinstance(data.get("data"), str):
        data["data"] = read((base / data["data"]).resolve())
    sources = data.get("sources", data.get("data", {}).get("sources", {}))
    if command == "market":
        from market_calculations import calculate_market
        check_sources(sources, references(data["records"]))
        result = calculate_market(data)
    elif command == "chart":
        from chapter_charts import explicit_chart
        result, blob = explicit_chart(data["chart"], data["data"], data.get("labels", {}))
        check_sources(sources, result.get("evidence_ids", []), require_st=False)
        check_sources(sources, result.get("data_evidence_ids", []))
        result["sources"] = sources
        result["data_hash"] = digest(data["data"])
        result["input_hash"] = digest(payload)
        return result, blob
    else:
        raise ValueError("unknown atomic tool")
    result["sources"] = sources
    result["input_hash"] = digest(payload)
    return result, None


def main():
    parser = argparse.ArgumentParser(description="Explicit calculations, SVG charts and dual HTML delivery")
    parser.add_argument("tool", choices=("market", "chart", "html", "validate"))
    parser.add_argument("--input", required=True, help="Explicit JSON input prepared from actual MCP results")
    parser.add_argument("--output", required=True, help="JSON result, SVG, or delivery directory for html/validate")
    parser.add_argument("--metadata", help="Optional chart metadata, or an HTML metadata file inside the output directory")
    parser.add_argument(
        "--script-mode",
        choices=("zero_js", "whitelisted_inline_js"),
        default=None,
        help="Optional script policy override for html/validate (defaults to manifest script_mode or zero_js)",
    )
    args = parser.parse_args()
    input_path, output_path = Path(args.input).resolve(), Path(args.output).resolve()
    metadata = Path(args.metadata).resolve() if args.metadata else None
    try:
        if output_path == input_path or metadata in {input_path, output_path}:
            raise ValueError("input, output and metadata paths must be distinct")
        if metadata and args.tool not in {"chart", "html"}:
            raise ValueError("--metadata is only used with chart or html")
        if args.script_mode and args.tool not in {"html", "validate"}:
            raise ValueError("--script-mode is only used with html or validate")
        payload = read(input_path)
        effective_script_mode = args.script_mode or payload.get("script_mode") or "zero_js"
        if args.tool == "validate":
            from html_delivery import validate
            result = validate(output_path, payload, base_dir=input_path.parent, script_mode=effective_script_mode)
            print(json.dumps(result, ensure_ascii=False))
            if result["status"] != "checked":
                raise SystemExit(2)
            return
        if args.tool == "html":
            from html_delivery import render
            result = render(
                output_path,
                payload,
                base_dir=input_path.parent,
                metadata_path=metadata,
                manifest_path=input_path,
                script_mode=effective_script_mode,
            )
            print(json.dumps(result, ensure_ascii=False))
            return
        result, blob = run_tool(args.tool, payload, base_dir=input_path.parent)
        if args.tool == "chart":
            if blob is None:
                print(json.dumps(result, ensure_ascii=False))
                raise SystemExit(2)
            if output_path.suffix.lower() != ".svg":
                raise ValueError("chart output must be .svg")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            extras = result.pop("_extra_blobs", [])
            written = [output_path]
            for index, extra in enumerate(extras, 2):
                written.append(output_path.with_name(f"{output_path.stem}-part{index}{output_path.suffix}"))
            for path, raw in zip(written, [blob, *extras]):
                temporary = path.with_name(path.name + ".tmp")
                temporary.write_bytes(raw)
                temporary.replace(path)
            result["file"] = str(output_path)
            if extras:
                result["files"] = [str(path) for path in written]
            if metadata:
                write(metadata, result)
        else:
            write(output_path, result)
        print(json.dumps({"status": result.get("status", "calculated"), "output": str(output_path),
                          "input_hash": result["input_hash"]}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()

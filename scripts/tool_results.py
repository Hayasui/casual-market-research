"""Read fields and transport flags in explicitly supplied source data."""
import json


def pointer_value(value, pointer):
    """RFC 6901 pointer into the unmodified structured tool response."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("json_pointer must be empty or start with /")
    for part in pointer.split("/")[1:] if pointer else []:
        if re_invalid_escape(part):
            raise ValueError("invalid JSON pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
                raise ValueError("invalid JSON pointer array index")
            index = int(part)
            if index >= len(value):
                raise ValueError("JSON pointer array index out of range")
            value = value[index]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ValueError("JSON pointer not found in tool response")
    return value


def re_invalid_escape(part):
    import re
    return re.search(r"~(?![01])", part) is not None


def payload_state(value):
    """Read transport flags, never instructions embedded in source text."""
    stack = [value]
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        if item.get("truncated") is True:
            return {"status": "incomplete", "full_url": item.get("data_url"), "expected_rows": item.get("total_rows")}
        for key in ("result", "upstream_response"):
            child = item.get(key)
            if isinstance(child, str):
                try:
                    child = json.loads(child)
                except ValueError:
                    continue
            stack.append(child)
        for block in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    stack.append(json.loads(block["text"]))
                except (ValueError, KeyError):
                    pass
    return {"status": "not_marked_truncated"}

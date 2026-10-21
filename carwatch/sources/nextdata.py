"""Helpers for Next.js App Router pages, which stream their data as self.__next_f.push([1, "..."]) chunks.
The decoded payload is lines of `id:json`; values like "$7a" point at another line."""
import json
import re


def next_payload(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return "".join(json.loads(f'"{c}"') for c in chunks)


def payload_rows(payload: str) -> dict:
    rows = {}
    for line in payload.split("\n"):
        m = re.match(r"^([0-9a-f]+):(.*)$", line)
        if m:
            try:
                rows[m.group(1)] = json.loads(m.group(2))
            except ValueError:
                pass
    return rows


def resolve(value, rows: dict, depth: int = 0):
    """Replace "$xx" references with the rows they point to (a few levels deep)."""
    if depth > 4:
        return value
    if isinstance(value, str) and re.fullmatch(r"\$[0-9a-f]+", value):
        return resolve(rows.get(value[1:]), rows, depth + 1)
    if isinstance(value, dict):
        return {k: resolve(v, rows, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, rows, depth + 1) for v in value]
    return value


def json_objects(payload: str, start_pattern: str):
    """Every JSON object in the payload that starts with start_pattern."""
    dec = json.JSONDecoder()
    for m in re.finditer(start_pattern, payload):
        try:
            yield dec.raw_decode(payload, m.start())[0]
        except ValueError:
            continue

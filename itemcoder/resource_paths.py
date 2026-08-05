"""Central app-resource path helpers.

Only JS and CSS stay inside static/.  JSON and CSV files are backend-managed
configuration/data files, so they live under itemcoder/resources/.

These helpers also accept older saved paths such as
``itemcoder/static/itemcoder/json/...`` and transparently map them to the new
resources folder.  This keeps old JSON configs working without changing the
processing logic.
"""
from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings

APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = APP_DIR / "resources"
JSON_DIR = RESOURCE_DIR / "json"
CSV_DIR = RESOURCE_DIR / "csv"


def resource_path(*parts: str) -> str:
    """Return an absolute path inside itemcoder/resources."""
    return str(RESOURCE_DIR.joinpath(*[str(p) for p in parts]))


def json_path(filename: str) -> str:
    """Return an absolute path for a JSON resource file."""
    return str(JSON_DIR / filename)


def csv_path(*parts: str) -> str:
    """Return an absolute path for a CSV resource file."""
    return str(CSV_DIR.joinpath(*[str(p) for p in parts]))


def resolve_resource_path(path_value: str) -> str:
    """Resolve configured JSON/CSV paths after moving data out of static.

    Supports:
    - absolute paths;
    - new paths: itemcoder/resources/...
    - old paths: itemcoder/static/itemcoder/json|csv/...
    - old path: itemcoder/json/data.json
    - relative paths used by existing configuration files.
    """
    value = str(path_value or "").strip()
    if not value:
        return ""

    if os.path.isabs(value):
        return value

    normalized = value.replace("\\", "/").lstrip("/")

    old_static_json = "itemcoder/static/itemcoder/json/"
    old_static_csv = "itemcoder/static/itemcoder/csv/"
    old_app_json = "itemcoder/json/"
    # Legacy typo folder ``itmecoder`` (and its static/ path) → csv/legacy/.
    old_itmecoder_static = "itemcoder/static/itmecoder/"
    old_itmecoder_res = "itemcoder/resources/itmecoder/"

    if normalized.startswith(old_static_json):
        return str(JSON_DIR / normalized[len(old_static_json):])
    if normalized.startswith(old_static_csv):
        return str(CSV_DIR / normalized[len(old_static_csv):])
    if normalized.startswith(old_app_json):
        return str(JSON_DIR / normalized[len(old_app_json):])
    if normalized.startswith(old_itmecoder_static):
        rest = normalized[len(old_itmecoder_static):]
        # old: .../css/pipe/foo.csv → csv/legacy/pipe/foo.csv
        if rest.startswith("css/"):
            rest = rest[len("css/"):]
        return str(CSV_DIR / "legacy" / rest)
    if normalized.startswith(old_itmecoder_res):
        rest = normalized[len(old_itmecoder_res):]
        if rest.startswith("css/"):
            rest = rest[len("css/"):]
        return str(CSV_DIR / "legacy" / rest)

    new_prefix = "itemcoder/resources/"
    if normalized.startswith(new_prefix):
        return str(RESOURCE_DIR / normalized[len(new_prefix):])

    # Preserve any non-standard relative project paths.
    return os.path.join(settings.BASE_DIR, *normalized.split("/"))

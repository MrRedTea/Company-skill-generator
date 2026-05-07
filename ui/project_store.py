"""Project save/load helpers for 算疏智合 GUI."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_SCHEMA = "suanshu-zhihe-project"
PROJECT_SCHEMA_VERSION = 1
PROJECT_FILE_SUFFIX = ".sszh.json"

_SECRET_KEYS = {"api_key", "authorization", "token", "secret", "password"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(secret in key_text for secret in _SECRET_KEYS):
                redacted[key] = ""
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def make_project_document(payload: dict[str, Any], project_name: str | None = None) -> dict[str, Any]:
    """Wrap the current GUI request payload into a durable project document.

    API keys and token-like values are intentionally removed. The user should
    re-enter secrets after reopening a project.
    """
    safe_payload = _redact(payload)
    title = project_name or safe_payload.get("company_name") or "未命名项目"
    return {
        "schema": PROJECT_SCHEMA,
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_name": title,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "payload": safe_payload,
    }


def extract_payload(document: dict[str, Any]) -> dict[str, Any]:
    """Return a request payload from either a project envelope or a raw payload."""
    if document.get("schema") == PROJECT_SCHEMA and isinstance(document.get("payload"), dict):
        return dict(document["payload"])
    if isinstance(document.get("payload"), dict):
        return dict(document["payload"])
    return dict(document)


def save_project_file(path: str | Path, payload: dict[str, Any], project_name: str | None = None) -> Path:
    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target.with_suffix(PROJECT_FILE_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = make_project_document(payload, project_name=project_name)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_project_file(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(path)
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("项目文件格式无效：顶层内容不是 JSON 对象。")
    payload = extract_payload(document)
    if not isinstance(payload, dict):
        raise ValueError("项目文件格式无效：缺少 payload。")
    return document, payload


def default_project_filename(company_name: str | None, company_slug: str | None) -> str:
    slug = (company_slug or "").strip() or "suanshu-zhihe-project"
    if not slug.endswith(PROJECT_FILE_SUFFIX):
        return f"{slug}{PROJECT_FILE_SUFFIX}"
    return slug

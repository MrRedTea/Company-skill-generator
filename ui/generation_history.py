"""Append-only generation history utilities for 算疏智合.

Stores every successful generation as one JSON object per line so the
GUI can show past outputs without scanning the companies directory repeatedly.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

HISTORY_DIR = Path("history")
HISTORY_FILE = HISTORY_DIR / "generation-history.jsonl"
MAX_HISTORY_RECORDS = 200


def history_file_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else Path.cwd()
    return root / HISTORY_FILE


def _coerce_path(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(Path(str(value)))
    except Exception:
        return str(value)


def append_generation_record(record: dict[str, Any], base_dir: str | Path | None = None) -> Path:
    """Append one generation record to the JSON Lines history file."""
    path = history_file_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = dict(record)
    clean.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    clean["output_dir"] = _coerce_path(clean.get("output_dir"))
    clean["log_path"] = _coerce_path(clean.get("log_path"))
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def load_generation_history(base_dir: str | Path | None = None, limit: int = MAX_HISTORY_RECORDS) -> list[dict[str, Any]]:
    """Load recent generation records, newest first.

    Invalid lines are skipped so one corrupted record does not break the viewer.
    """
    path = history_file_path(base_dir)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            records.append(item)
    records.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return records[: max(1, int(limit or MAX_HISTORY_RECORDS))]


def format_history_item(record: dict[str, Any]) -> str:
    created_at = str(record.get("created_at") or "未知时间").replace("T", " ")
    company = str(record.get("company_name") or record.get("company_slug") or "未命名公司")
    model = str(record.get("model") or record.get("local_model_name") or record.get("model_type") or "未记录模型")
    status = str(record.get("quality_status") or "unknown")
    status_label = {"pass": "通过", "warn": "警告", "fail": "失败", "unknown": "未检查"}.get(status, status)
    return f"{created_at} · {company} · {model} · {status_label}"


def format_history_detail(record: dict[str, Any]) -> str:
    lines = [
        f"时间：{str(record.get('created_at') or '未知').replace('T', ' ')}",
        f"公司：{record.get('company_name') or '未记录'}",
        f"标识：{record.get('company_slug') or '未记录'}",
        f"模型类型：{record.get('model_type') or '未记录'}",
        f"模型：{record.get('model') or record.get('local_model_name') or '未记录'}",
        f"质量状态：{record.get('quality_status') or '未检查'}",
        f"质量标题：{record.get('quality_title') or '未记录'}",
        f"输出目录：{record.get('output_dir') or '未记录'}",
        f"日志文件：{record.get('log_path') or '未记录'}",
    ]
    duration = record.get("duration_seconds")
    if duration is not None:
        lines.append(f"耗时：{duration} 秒")
    files = record.get("files") or []
    if files:
        lines.append("\n文件：")
        lines.extend(f"- {name}" for name in files)
    warnings = record.get("quality_warnings") or []
    if warnings:
        lines.append("\n质量提醒：")
        lines.extend(f"- {item}" for item in warnings[:12])
    return "\n".join(lines)

# -*- coding: utf-8 -*-
"""Configuration health checks for 算疏智合.

Provides a local, non-network configuration check designed to
catch missing inputs, broken file paths, malformed JSON, and missing project
resources before the user reaches the final generation step.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_PROMPTS = [
    "intake.md",
    "source_map.md",
    "boss_builder.md",
    "employee_collective_builder.md",
    "code_builder.md",
    "router_builder.md",
    "strategy_vision_builder.md",
    "career_path_builder.md",
    "PROMPT_MANIFEST.md",
    "cliche_rewrite_dictionary.md",
    "prompt_governance.md",
    "source_trace_protocol.md",
    "source_confidence_protocol.md",
    "runtime_compression_gate.md",
    "runtime_output_contract.md",
    "runtime_answer_cases.md",
]

RUNTIME_DIRECTORIES = [
    "companies",
    "preview_reports",
    "uploads",
    "agent_exports",
    "logs",
    "projects",
    "history",
    "template_library",
]

SECRET_KEYS = {"api_key", "token", "secret", "password", "authorization"}


@dataclass(frozen=True)
class HealthItem:
    level: str  # ok / warn / error
    title: str
    detail: str = ""

    @property
    def icon(self) -> str:
        return {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(self.level, "•")



def _as_path_list(value: Any) -> list[Path]:
    if not value:
        return []
    if isinstance(value, (str, Path)):
        value = [value]
    if not isinstance(value, list):
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        paths.append(Path(text))
    return paths


def _check_file_list(items: list[HealthItem], label: str, paths: list[Path], *, warn_if_empty: str | None = None) -> None:
    if not paths:
        if warn_if_empty:
            items.append(HealthItem("warn", label, warn_if_empty))
        return
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        preview = "\n".join(missing[:5])
        suffix = f"\n…另有 {len(missing) - 5} 个" if len(missing) > 5 else ""
        items.append(HealthItem("error", f"{label}存在缺失路径", preview + suffix))
    else:
        items.append(HealthItem("ok", f"{label}路径有效", f"共 {len(paths)} 个文件。"))


def _member_paths(payload: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    personal = payload.get("personal_distillation") or {}
    for key in ("file_path", "file_paths", "model_file_path", "model_file_paths"):
        paths.extend(_as_path_list(personal.get(key)))
    for member in payload.get("organization_members") or []:
        if not isinstance(member, dict):
            continue
        for key in (
            "profile_file_path", "profile_file_paths", "file_path", "file_paths",
            "skill_file_path", "skill_file_paths", "model_file_path", "model_file_paths",
        ):
            paths.extend(_as_path_list(member.get(key)))
    # de-duplicate while preserving order
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        text = str(path)
        if text not in seen:
            unique.append(path)
            seen.add(text)
    return unique


def _check_json_file(items: list[HealthItem], path: Path, label: str, *, optional: bool = False) -> None:
    if not path.exists():
        if optional:
            items.append(HealthItem("ok", f"{label}未创建", "该文件是可选文件，缺失不会影响首次运行。"))
        else:
            items.append(HealthItem("warn", f"{label}不存在", str(path)))
        return
    try:
        json.loads(path.read_text(encoding="utf-8"))
        items.append(HealthItem("ok", f"{label}可解析", str(path)))
    except Exception as exc:
        items.append(HealthItem("error", f"{label}无法解析", f"{path}\n{exc}"))


def _can_write_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return os.access(path, os.W_OK)


def run_config_health_check(payload: dict[str, Any], *, project_root: Path | None = None, model_test_ok: bool | None = None, model_test_message: str = "") -> dict[str, Any]:
    """Return a local configuration health report.

    This checker deliberately avoids network calls.  The model connection check is
    handled separately by the GUI because it can take time and must support
    cancellation.
    """
    root = project_root or Path.cwd()
    items: list[HealthItem] = []

    company_name = str(payload.get("company_name") or "").strip()
    company_slug = str(payload.get("company_slug") or "").strip()
    if company_name:
        items.append(HealthItem("ok", "公司名称已填写", company_name))
    else:
        items.append(HealthItem("error", "公司名称未填写", "请在第 1 步填写公司名称。"))
    if company_slug and company_slug.lower() not in {"company", "default"}:
        items.append(HealthItem("ok", "公司标识可用", company_slug))
    else:
        items.append(HealthItem("warn", "公司标识可能需要确认", "建议使用可识别的英文/拼音短标识，避免 company、default 等通用值。"))

    model_type = str(payload.get("model_type") or "cloud").strip().lower()
    if model_type == "cloud":
        provider = str(payload.get("provider") or "").strip()
        api_base = str(payload.get("api_base") or "").strip()
        model = str(payload.get("model") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()
        if provider:
            items.append(HealthItem("ok", "云端服务商已填写", provider))
        else:
            items.append(HealthItem("warn", "云端服务商未填写", "建议填写 deepseek、openai 或自定义服务商名称。"))
        if api_key:
            items.append(HealthItem("ok", "API Key 已输入", "配置体检不会保存或展示完整密钥。"))
        else:
            items.append(HealthItem("error", "API Key 未填写", "云端模式需要 API Key。"))
        if api_base:
            items.append(HealthItem("ok", "API Base 已填写", api_base))
        else:
            items.append(HealthItem("error", "API Base 未填写", "例如 https://api.deepseek.com。"))
        if model:
            items.append(HealthItem("ok", "模型名称已填写", model))
        else:
            items.append(HealthItem("error", "模型名称未填写", "请填写服务商支持的模型名。"))
    else:
        local_name = str(payload.get("local_model_name") or "").strip()
        local_host = str(payload.get("local_host") or "").strip()
        if local_name:
            items.append(HealthItem("ok", "本地模型名称已填写", local_name))
        else:
            items.append(HealthItem("error", "本地模型名称未填写", "请填写或自动识别本地模型。"))
        if local_host:
            items.append(HealthItem("ok", "本地模型服务地址已填写", local_host))
        else:
            items.append(HealthItem("error", "本地模型服务地址未填写", "例如 http://localhost:11434。"))

    if model_test_ok is True:
        items.append(HealthItem("ok", "模型连接检查已通过", model_test_message or "最近一次模型连接检查成功。"))
    elif model_test_ok is False:
        items.append(HealthItem("warn", "最近一次模型连接检查未通过", model_test_message or "建议重新检查模型连接。"))
    else:
        items.append(HealthItem("warn", "尚未确认模型连接", "建议点击“检查模型连接”，正式生成前确认模型可用。"))

    _check_file_list(items, "公司资料文件", _as_path_list(payload.get("company_files")), warn_if_empty="没有上传公司资料也可以生成，但输出会更依赖模型通用知识。")
    org = payload.get("organization") or {}
    selected = list(org.get("selected_departments") or [])
    custom = list(org.get("custom_departments") or [])
    if len(selected) + len(custom) >= 4:
        items.append(HealthItem("ok", "组织部门配置较完整", f"标准部门 {len(selected)} 个，自定义部门 {len(custom)} 个。"))
    else:
        items.append(HealthItem("warn", "组织部门配置偏少", "建议至少保留 4 个以上核心部门，或上传组织架构图。"))
    _check_file_list(items, "组织架构资料", _as_path_list(org.get("org_chart_files")))
    _check_file_list(items, "组织人员 / Skill 文件", _member_paths(payload))

    prompts_dir = root / "prompts"
    if not prompts_dir.exists():
        items.append(HealthItem("error", "prompts 目录缺失", str(prompts_dir)))
    else:
        missing_prompts = [name for name in REQUIRED_PROMPTS if not (prompts_dir / name).exists()]
        if missing_prompts:
            items.append(HealthItem("error", "Prompt 文件缺失", "、".join(missing_prompts)))
        else:
            items.append(HealthItem("ok", "Prompt 文件完整", f"共 {len(REQUIRED_PROMPTS)} 个必要 prompt。"))

    missing_dirs = [name for name in RUNTIME_DIRECTORIES if not (root / name).exists()]
    if missing_dirs:
        items.append(HealthItem("warn", "运行目录尚未完整创建", "、".join(missing_dirs)))
    else:
        not_writable = [name for name in RUNTIME_DIRECTORIES if not _can_write_dir(root / name)]
        if not_writable:
            items.append(HealthItem("error", "部分运行目录不可写", "、".join(not_writable)))
        else:
            items.append(HealthItem("ok", "运行目录可写", "输出、日志、项目和模板目录均存在并可写。"))

    _check_json_file(items, root / "config.json", "config.json", optional=True)
    _check_json_file(items, root / "template_library" / "custom_templates.json", "自定义模板库", optional=True)

    errors = sum(1 for item in items if item.level == "error")
    warnings = sum(1 for item in items if item.level == "warn")
    ok_count = sum(1 for item in items if item.level == "ok")
    if errors:
        status = "error"
        summary = f"发现 {errors} 个错误、{warnings} 个提醒。建议先修复错误再生成。"
    elif warnings:
        status = "warn"
        summary = f"未发现阻断性错误，但有 {warnings} 个提醒。可以继续，但建议确认。"
    else:
        status = "ok"
        summary = "配置体检通过，未发现明显问题。"

    return {
        "status": status,
        "summary": summary,
        "counts": {"ok": ok_count, "warn": warnings, "error": errors},
        "items": [item.__dict__ | {"icon": item.icon} for item in items],
    }


def format_health_report(report: dict[str, Any]) -> str:
    counts = report.get("counts", {})
    lines = [
        f"配置体检：{report.get('summary', '')}",
        f"通过 {counts.get('ok', 0)} 项 · 提醒 {counts.get('warn', 0)} 项 · 错误 {counts.get('error', 0)} 项",
        "",
    ]
    for item in report.get("items") or []:
        detail = str(item.get("detail") or "").strip()
        lines.append(f"{item.get('icon', '•')} {item.get('title', '')}")
        if detail:
            for line in detail.splitlines():
                lines.append(f"   {line}")
    return "\n".join(lines).strip()

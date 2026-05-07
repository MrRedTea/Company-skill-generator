"""Output quality inspection helpers for 算疏智合.

This module keeps post-generation checks out of the main GUI file. The checks
are intentionally conservative: they do not judge content correctness, but they
catch common generation failures such as missing files, empty files, leaked
reasoning tags, invalid JSON, and unfinished markdown fences.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REQUIRED_FILES = [
    "SKILL.md",
    "boss-skill.md",
    "employee-collective-skill.md",
    "company-code.md",
    "organization-members.md",
]

RECOMMENDED_FILES = [
    "strategy-vision.md",
    "generation-summary.md",
    "generation-request.json",
]

TEXT_FILE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}

FAILURE_PHRASES = [
    "无法生成",
    "无法回答",
    "不能完成",
    "请提供更多",
    "抱歉",
    "对不起",
    "as an ai",
    "i cannot",
    "i'm unable",
    "cannot comply",
    "placeholder",
    "todo",
]


CLICHE_PHRASES = [
    "赋能", "闭环", "抓手", "高屋建瓴", "顶层设计", "行稳致远", "长期主义",
    "生态协同", "以客户为中心", "数据驱动", "打造标杆", "形成合力", "持续优化",
    "稳步推进", "全面赋能", "高度重视", "充分发挥", "统筹兼顾", "协同推进",
]

CUELESS_BOUNDARY_PHRASES = [
    "结合实际情况综合判断",
    "需要进一步结合实际情况",
    "加强沟通协同",
    "形成工作合力",
]

BACKGROUND_OPENERS = [
    "在当前复杂多变",
    "随着行业竞争",
    "在数字化转型",
    "面对日益激烈",
    "企业需要不断",
]

RUNTIME_REPORT_MARKERS = [
    "[问题归类]",
    "[组织插槽成员识别]",
    "[老板视角]",
    "[员工集合体视角]",
    "[组织插槽成员视角]",
    "[行为准则校验]",
    "[综合建议]",
    "[组织落地建议]",
    "[发言人简易版]",
    "[诚实边界]",
]

PHASE_HEADINGS = [
    "### 第一阶段（0-6个月）",
    "### 第二阶段（6-12个月）",
    "### 第三阶段（12-24个月）",
]

REQUIRED_SKILL_MODE_PHRASES = [
    "结论版",
    "分析版",
    "报告版",
]

REQUIRED_RUNTIME_CASE_PHRASES = [
    "合格",
    "不合格",
    "发言人",
    "资料不足",
]

REQUIRED_RUNTIME_CONTRACT_PHRASES = [
    "决策卡",
    "冲突卡",
    "口径卡",
    "缺口卡",
]

REQUIRED_COMPRESSION_GATE_PHRASES = [
    "回答前压缩门",
    "背景式开头",
    "最小动作",
    "自检过程",
]

REQUIRED_SOURCE_CONFIDENCE_PHRASES = [
    "证据强度",
    "强依据",
    "弱依据",
    "推断",
    "模板默认",
    "未知",
]

REQUIRED_SOURCE_TRACE_PHRASES = [
    "资料短链",
    "判断来源",
    "来源短链",
    "上传资料",
    "用户说明",
    "组织人员资料",
]


REQUIRED_PROMPT_GOVERNANCE_PHRASES = [
    "提示词协议优先级",
    "规则冲突裁决",
    "资料边界优先",
    "运行时输出合同优先",
    "表达压缩优先",
]

REQUIRED_CLICHE_REWRITE_PHRASES = [
    "抽象词",
    "owner",
    "节点",
    "审批",
    "指标",
]

REQUIRED_MODEL_ADAPTATION_PHRASES = [
    "模型适配",
    "强指令模型",
    "通用云端模型",
    "本地",
    "弱指令模型",
    "推理型模型",
]

REQUIRED_PROMPT_RELEASE_PHRASES = [
    "Prompt 发布冻结标记",
    "发布前冻结",
    "修改必须说明",
    "检查覆盖",
]

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{12,}", re.I),
]


@dataclass
class QualityItem:
    level: str  # pass / warn / fail
    title: str
    detail: str = ""


def _safe_read(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _add(items: list[QualityItem], level: str, title: str, detail: str = "") -> None:
    items.append(QualityItem(level=level, title=title, detail=detail))


def _check_markdown_file(path: Path, text: str, items: list[QualityItem]) -> None:
    lower = text.lower()
    if "<think>" in lower or "</think>" in lower:
        _add(items, "fail", f"{path.name} 存在推理标签残留", "检测到 <think> 或 </think>，建议重新生成或清理输出。")
    if text.count("```") % 2 == 1:
        _add(items, "warn", f"{path.name} 可能存在未闭合代码块", "检测到 Markdown 代码围栏数量为奇数。")
    for phrase in FAILURE_PHRASES:
        if phrase in lower:
            _add(items, "warn", f"{path.name} 可能包含失败话术", f"命中关键词：{phrase}")
            break
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            _add(items, "fail", f"{path.name} 可能包含密钥", "检测到疑似 API Key 或 Bearer Token，请立即检查输出文件。")
            break
    if path.suffix.lower() == ".md" and "#" not in text[:4000]:
        _add(items, "warn", f"{path.name} 缺少明显 Markdown 标题", "文件前部未发现标题标记，可能结构不完整。")




def _check_structure_duplicates(path: Path, text: str, items: list[QualityItem]) -> None:
    """Warn on duplicated headings/markers that usually indicate contract repair noise."""
    if path.suffix.lower() != ".md":
        return

    lines = [line.strip() for line in text.splitlines()]
    headings = [line for line in lines if re.match(r"^#{2,6}\s+", line)]
    duplicate_headings = sorted({heading for heading in headings if headings.count(heading) > 1})
    if duplicate_headings:
        _add(
            items,
            "warn",
            f"{path.name} 存在重复 Markdown 标题",
            "重复标题：" + "、".join(duplicate_headings[:8]) + "。这通常意味着章节修复或 prompt 拼接重复。",
        )

    if path.name == "SKILL.md":
        duplicate_markers = [marker for marker in RUNTIME_REPORT_MARKERS if lines.count(marker) > 1]
        if duplicate_markers:
            _add(
                items,
                "warn",
                "SKILL.md 存在重复运行时标记",
                "重复标记：" + "、".join(duplicate_markers) + "。建议检查 FILE_CONTRACTS 和章节修复逻辑。",
            )

    if path.name in {"career-path.md", "strategy-vision.md"}:
        duplicate_phases = [heading for heading in PHASE_HEADINGS if lines.count(heading) > 1]
        if duplicate_phases:
            _add(
                items,
                "warn",
                f"{path.name} 存在重复三阶段小标题",
                "重复小标题：" + "、".join(duplicate_phases) + "。建议检查父级章节和子级章节是否被重复拼接。",
            )


ANCHOR_TERMS = [
    "owner", "负责人", "责任人", "节点", "审批", "指标", "材料", "时间", "边界",
    "验收", "deadline", "周期", "输入", "输出", "复盘", "基线", "目标",
    "动作", "机制", "权限", "范围", "风险", "证据", "来源", "补充",
]

REFERENCE_SECTION_HEADINGS = {
    "## 套话替换词典",
    "## 运行时表达案例",
    "## 标准输出格式",
    "## 反模式",
    "### 最反感什么类型的借口",
    "### 最反感什么类型的汇报",
    "### 什么方案一看就会被否",
    "### 什么叫伪战略",
    "### 什么叫“正确但无用”的表达",
    "### 常见组织黑话",
    "### 哪些表达一听就像外行",
    "### 常见低质量表达",
}

REFERENCE_SECTION_PATTERNS = [
    re.compile(r"^##\s*反模式\s*$"),
    re.compile(r"^###\s*最反感什么"),
    re.compile(r"^###\s*绝不会怎么说"),
    re.compile(r"^###\s*什么方案一看就会被否"),
    re.compile(r"^###\s*什么叫伪战略"),
    re.compile(r"^###\s*什么叫[“\"]?正确但无用"),
    re.compile(r"^###\s*哪些表达一听就像外行"),
    re.compile(r"^###\s*常见低质量表达"),
    re.compile(r"^###\s*常见组织黑话"),
]

REFERENCE_UNIT_MARKERS = (
    "不合格", "禁用", "避免", "删除", "不得", "替换", "抽象词", "套话",
    "反模式", "最反感", "绝不会", "一看就会被否", "伪战略", "正确但无用",
    "外行", "低质量表达", "组织黑话", "不会说", "不会接受", "不能", "不绑定",
    "不存在", "例外情况", "适用边界", "失效边界", "价值观", "核心价值观",
    "为什么会形成", "典型体现", "会先看什么", "如“", "例如", "即", "我们一定要",
)


def _is_reference_heading(stripped_line: str) -> bool:
    return stripped_line in REFERENCE_SECTION_HEADINGS or any(
        pattern.search(stripped_line) for pattern in REFERENCE_SECTION_PATTERNS
    )


def _heading_level(stripped_line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+", stripped_line)
    if not match:
        return None
    return len(match.group(1))


def _strip_reference_sections(text: str) -> str:
    """Remove sections that intentionally list bad examples or trigger words.

    Some skill files contain anti-examples such as ``伪战略`` or ``哪些表达一听
    就像外行``. Those sections should be allowed to mention corporate clichés
    because their purpose is to prohibit them, not to generate them at runtime.
    """
    lines = text.splitlines()
    kept: list[str] = []
    skipping_level: int | None = None
    for line in lines:
        stripped = line.strip()
        current_level = _heading_level(stripped)
        if current_level is not None and skipping_level is not None and current_level <= skipping_level:
            skipping_level = None
        if _is_reference_heading(stripped):
            skipping_level = current_level or 6
            continue
        if skipping_level is None:
            kept.append(line)
    return "\n".join(kept)


def _split_expression_units(text: str) -> list[str]:
    return [unit.strip() for unit in re.split(r"[。！？；;\n]+", text) if unit.strip()]


def _unanchored_cliche_hits(text: str) -> list[str]:
    """Return cliche terms that appear without nearby concrete anchors.

    The project deliberately keeps a cliche rewrite dictionary inside SKILL.md.
    Counting those words as quality warnings creates false positives, so this
    check only flags trigger words in executable prose where the same sentence
    lacks an owner/node/material/metric/time/boundary style anchor.
    """
    scan_text = _strip_reference_sections(text)
    hits: list[str] = []
    for unit in _split_expression_units(scan_text):
        found = [phrase for phrase in CLICHE_PHRASES if phrase in unit]
        if not found:
            continue
        anchored = any(anchor in unit for anchor in ANCHOR_TERMS)
        negated_or_meta = any(marker in unit for marker in REFERENCE_UNIT_MARKERS)
        quoted_or_explained = re.search(r"[“\"'‘]?(?:" + "|".join(map(re.escape, found)) + r")[”\"'’]?\s*(?:=|＝|是|指|类|这种|这类)", unit)
        if anchored or negated_or_meta or quoted_or_explained:
            continue
        for phrase in found:
            if phrase not in hits:
                hits.append(phrase)
    return hits


def _check_expression_quality(path: Path, text: str, items: list[QualityItem]) -> None:
    """Warn when generated skill text is likely to produce boilerplate answers."""
    if path.suffix.lower() != ".md":
        return
    if path.name not in {"SKILL.md", "boss-skill.md", "employee-collective-skill.md", "company-code.md", "strategy-vision.md", "career-path.md"}:
        return
    hits = _unanchored_cliche_hits(text)
    # A single abstract term can be acceptable; many unanchored terms usually
    # means the skill will produce corporate boilerplate when invoked.
    if len(hits) >= 5:
        _add(
            items,
            "warn",
            f"{path.name} 可能包含较多未落地套话触发词",
            "命中：" + "、".join(hits[:10]) + "。建议把这些词绑定到 owner、节点、审批、指标、材料、时间或边界。",
        )
    weak_boundaries = [phrase for phrase in CUELESS_BOUNDARY_PHRASES if phrase in text]
    if weak_boundaries:
        _add(
            items,
            "warn",
            f"{path.name} 可能存在无效边界/协同套话",
            "命中：" + "、".join(weak_boundaries[:5]) + "。建议改成具体资料缺口、责任人、审批节点或下一步动作。",
        )
    if path.name == "SKILL.md":
        missing_modes = [phrase for phrase in REQUIRED_SKILL_MODE_PHRASES if phrase not in text]
        if missing_modes:
            _add(
                items,
                "warn",
                "SKILL.md 缺少回答长度档位",
                "缺少：" + "、".join(missing_modes) + "。建议内置结论版/分析版/报告版，避免默认输出过长。",
            )
        if "冲突点" not in text or "最小动作" not in text:
            _add(
                items,
                "warn",
                "SKILL.md 缺少冲突裁决模板",
                "建议明确输出冲突点、优先级和最小动作，避免把真实冲突写成“加强协同”。",
            )
        missing_cases = [phrase for phrase in REQUIRED_RUNTIME_CASE_PHRASES if phrase not in text]
        if missing_cases:
            _add(
                items,
                "warn",
                "SKILL.md 缺少运行时表达案例",
                "缺少：" + "、".join(missing_cases) + "。建议加入合格/不合格表达样例，约束后续调用时少说套话。",
            )
        missing_contracts = [phrase for phrase in REQUIRED_RUNTIME_CONTRACT_PHRASES if phrase not in text]
        if missing_contracts:
            _add(
                items,
                "warn",
                "SKILL.md 缺少运行时输出合同",
                "缺少：" + "、".join(missing_contracts) + "。建议内置决策卡/冲突卡/口径卡/缺口卡，避免运行时误写成长报告。",
            )
        missing_gate = [phrase for phrase in REQUIRED_COMPRESSION_GATE_PHRASES if phrase not in text]
        if missing_gate:
            _add(
                items,
                "warn",
                "SKILL.md 缺少回答前压缩门",
                "缺少：" + "、".join(missing_gate) + "。建议内置回答前压缩门，确保运行时先删背景、套话和无动作句，再输出最终答案。",
            )

        missing_source_confidence = [phrase for phrase in REQUIRED_SOURCE_CONFIDENCE_PHRASES if phrase not in text]
        if missing_source_confidence:
            _add(
                items,
                "warn",
                "SKILL.md 缺少证据强度与资料边界",
                "缺少：" + "、".join(missing_source_confidence) + "。建议内置证据强度规则，避免把推断、模板默认或未知写成确定事实。",
            )

        missing_source_trace = [phrase for phrase in REQUIRED_SOURCE_TRACE_PHRASES if phrase not in text]
        if missing_source_trace:
            _add(
                items,
                "warn",
                "SKILL.md 缺少资料短链与判断来源",
                "缺少：" + "、".join(missing_source_trace) + "。建议内置资料短链规则，确保关键判断可追溯但运行时不变成长引用报告。",
            )

        missing_governance = [phrase for phrase in REQUIRED_PROMPT_GOVERNANCE_PHRASES if phrase not in text]
        if missing_governance:
            _add(
                items,
                "warn",
                "SKILL.md 缺少提示词协议优先级",
                "缺少：" + "、".join(missing_governance) + "。建议内置协议优先级与规则冲突裁决，避免短答、证据、冲突和完整结构互相打架。",
            )

        missing_cliche_rewrite = [phrase for phrase in REQUIRED_CLICHE_REWRITE_PHRASES if phrase not in text]
        if missing_cliche_rewrite:
            _add(
                items,
                "warn",
                "SKILL.md 缺少套话替换规则",
                "缺少：" + "、".join(missing_cliche_rewrite) + "。建议要求抽象词必须绑定 owner、节点、审批、指标、材料、时间或边界。",
            )

        missing_model_adaptation = [phrase for phrase in REQUIRED_MODEL_ADAPTATION_PHRASES if phrase not in text]
        if missing_model_adaptation:
            _add(
                items,
                "warn",
                "SKILL.md 缺少模型适配策略",
                "缺少：" + "、".join(missing_model_adaptation) + "。建议内置强指令/通用云端/本地弱指令/推理型模型的降级策略，避免换模型后重新变长或输出思考过程。",
            )

        missing_prompt_release = [phrase for phrase in REQUIRED_PROMPT_RELEASE_PHRASES if phrase not in text]
        if missing_prompt_release:
            _add(
                items,
                "warn",
                "SKILL.md 缺少 Prompt 发布冻结标记",
                "缺少：" + "、".join(missing_prompt_release) + "。建议内置发布冻结规则，防止后续提示词继续堆叠或误删核心运行时协议。",
            )

    opener_hits = [phrase for phrase in BACKGROUND_OPENERS if phrase in text[:800]]
    if opener_hits:
        _add(
            items,
            "warn",
            f"{path.name} 可能存在背景式开头",
            "命中：" + "、".join(opener_hits) + "。建议第一句直接给结论，除非用户明确询问背景。",
        )

def inspect_output_quality(output_dir: str | Path | None) -> dict:
    """Inspect generated output files and return a serializable report."""
    items: list[QualityItem] = []
    out_path = Path(str(output_dir)) if output_dir else None

    if not out_path:
        _add(items, "fail", "没有输出目录", "生成流程未返回输出目录。")
    elif not out_path.exists():
        _add(items, "fail", "输出目录不存在", str(out_path))
    elif not out_path.is_dir():
        _add(items, "fail", "输出路径不是目录", str(out_path))
    else:
        files = {p.name: p for p in out_path.iterdir() if p.is_file()}
        for name in REQUIRED_FILES:
            if name not in files:
                _add(items, "fail", f"缺少必要文件：{name}")
        for name in RECOMMENDED_FILES:
            if name not in files:
                _add(items, "warn", f"缺少建议文件：{name}")

        request_payload = {}
        if "generation-request.json" in files:
            try:
                request_payload = json.loads(_safe_read(files["generation-request.json"]))
            except Exception:
                request_payload = {}
        personal_enabled = bool((request_payload.get("personal_distillation") or {}).get("enabled"))
        has_org_members = bool(request_payload.get("organization_members"))
        if (personal_enabled or has_org_members) and "career-path.md" not in files:
            _add(items, "warn", "缺少建议文件：career-path.md", "已启用组织人员蒸馏或组织成员插入时，建议生成职业路径/成员发展预测。")

        if not files:
            _add(items, "fail", "输出目录为空", str(out_path))

        for path in sorted(files.values(), key=lambda p: p.name.lower()):
            try:
                size = path.stat().st_size
            except OSError as exc:
                _add(items, "warn", f"无法读取文件状态：{path.name}", str(exc))
                continue
            if size == 0:
                _add(items, "fail", f"空文件：{path.name}")
                continue
            if path.name in REQUIRED_FILES and size < 80:
                _add(items, "warn", f"{path.name} 内容过短", f"当前大小 {size} 字节，建议人工检查。")
            if path.suffix.lower() in TEXT_FILE_SUFFIXES:
                try:
                    text = _safe_read(path)
                except Exception as exc:  # pragma: no cover - defensive
                    _add(items, "warn", f"无法读取文本文件：{path.name}", str(exc))
                    continue
                if path.suffix.lower() == ".json":
                    try:
                        json.loads(text)
                    except Exception as exc:
                        _add(items, "warn", f"JSON 格式异常：{path.name}", str(exc))
                _check_markdown_file(path, text, items)
                _check_structure_duplicates(path, text, items)
                _check_expression_quality(path, text, items)

        if not any(item.level == "fail" for item in items):
            required_count = sum(1 for name in REQUIRED_FILES if name in files)
            _add(items, "pass", "必要文件检查通过", f"已发现 {required_count}/{len(REQUIRED_FILES)} 个必要文件。")

    fail_count = sum(1 for item in items if item.level == "fail")
    warn_count = sum(1 for item in items if item.level == "warn")
    pass_count = sum(1 for item in items if item.level == "pass")
    if fail_count:
        status = "fail"
        title = f"发现 {fail_count} 个严重问题"
    elif warn_count:
        status = "warn"
        title = f"发现 {warn_count} 个建议检查项"
    else:
        status = "pass"
        title = "生成质量检查通过"

    return {
        "status": status,
        "title": title,
        "summary": {"pass": pass_count, "warn": warn_count, "fail": fail_count},
        "items": [item.__dict__ for item in items],
    }


def format_quality_report(report: dict) -> str:
    """Format an inspection report for display in the GUI."""
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    lines = [report.get("title") or "生成质量检查", ""]
    for item in report.get("items", []):
        level = item.get("level", "warn")
        title = item.get("title", "")
        detail = item.get("detail", "")
        line = f"{icons.get(level, '•')} {title}"
        if detail:
            line += f"\n   {detail}"
        lines.append(line)
    return "\n".join(lines).strip()

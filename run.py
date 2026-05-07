import argparse
import json
import logging
import os
import locale
import re
import shutil
import sys
from datetime import datetime
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent_exporter import export_agent_packages


def configure_stdio_encoding() -> None:
    """Keep redirected stdout/stderr UTF-8-safe on Windows GUI subprocess runs."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text_with_encoding_fallback(path: Path) -> str:
    encodings = ("utf-8", "utf-8-sig", "gb18030", "cp936", locale.getpreferredencoding(False), "latin-1")
    tried: set[str] = set()
    for encoding in encodings:
        if not encoding or encoding in tried:
            continue
        tried.add(encoding)
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


configure_stdio_encoding()

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from project_utils import (
    DEFAULT_DEPARTMENTS,
    build_preview_prompt,
    detect_org_chart_files,
    auto_select_local_model,
    build_request_context,
    classify_employee_file_department,
    classify_employee_files_department,
    format_file_names,
    format_search_results,
    merge_file_path_values,
    normalize_api_base,
    search_latest_role_info,
    slugify,
    summarize_documents,
)

BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"
OUTPUT_BASE = BASE_DIR / "companies"
PREVIEW_DIR = BASE_DIR / "preview_reports"
AGENT_EXPORT_BASE = BASE_DIR / "agent_exports"
LOCAL_MODEL_TIMEOUT_SECONDS = 3600

log_file = BASE_DIR / "run.log"
LOGS_DIR = BASE_DIR / "logs"


def rotate_previous_run_log() -> Path | None:
    """Archive the previous run.log before starting a new run.

    Older releases appended every execution into the same run.log. That made the
    GUI and users see old warnings/errors from previous runs mixed with the
    current generation. Keep run.log focused on the current process and preserve
    the old file under logs/.
    """
    try:
        if not log_file.exists() or log_file.stat().st_size <= 0:
            return None
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = LOGS_DIR / f"run-{stamp}.log"
        counter = 1
        while archived.exists():
            archived = LOGS_DIR / f"run-{stamp}-{counter}.log"
            counter += 1
        shutil.move(str(log_file), str(archived))
        return archived
    except Exception:
        # Logging must never prevent the generator from starting. If archiving
        # fails, FileHandler(mode="w") below will still create a clean log.
        return None


_archived_previous_log = rotate_previous_run_log()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file, mode="w", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
if _archived_previous_log:
    logger.info("已归档上一轮运行日志: %s", _archived_previous_log)
logger.info("当前运行日志文件: %s", log_file)


INVALID_COMPANY_SLUGS = {
    "", "无", "none", "null", "nil", "n/a", "na", "未填写", "默认", "default",
    "company", "company-skill", "公司",
}
SENSITIVE_KEY_NAMES = {"api_key", "apikey", "api-key", "authorization", "access_token", "refresh_token", "token", "secret", "password", "client_secret"}
SECRET_VALUE_PATTERNS = (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{12,}", flags=re.I))


def is_invalid_company_slug(value: Any) -> bool:
    return str(value or "").strip().lower() in INVALID_COMPANY_SLUGS


def normalize_company_slug(raw_slug: Any, company_name: Any) -> str:
    candidate = slugify(str(raw_slug or "")) if raw_slug else ""
    if is_invalid_company_slug(candidate):
        candidate = slugify(str(company_name or ""))
    if is_invalid_company_slug(candidate):
        candidate = "company-skill"
    return candidate


def mask_secret_text(value: str) -> str:
    text = value
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub("***REDACTED***", text)
    return text




def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_remove_tree(path: Path, allowed_root: Path) -> bool:
    """Remove a generated artifact only when it is inside an allowed project folder."""
    target = path.resolve()
    root = allowed_root.resolve()
    if not target.exists():
        return False
    if not _is_relative_to(target, root) or target == root:
        raise ValueError(f"拒绝清理非受控路径：{target}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return True


def clean_generated_artifacts(company_slug: str) -> list[Path]:
    """Clean stale generated outputs for a company slug before regeneration."""
    slug = normalize_company_slug(company_slug, company_slug or "company-skill")
    removed: list[Path] = []
    for target, root in [
        (OUTPUT_BASE / slug, OUTPUT_BASE),
        (AGENT_EXPORT_BASE / slug, AGENT_EXPORT_BASE),
    ]:
        if safe_remove_tree(target, root):
            removed.append(target)

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    for preview_file in PREVIEW_DIR.glob(f"{slug}-preview-report.*"):
        if safe_remove_tree(preview_file, PREVIEW_DIR):
            removed.append(preview_file)
    return removed


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in SENSITIVE_KEY_NAMES or any(part in key_text for part in ("api_key", "secret", "password", "token", "authorization")):
                redacted[key] = "***REDACTED***" if item else item
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return mask_secret_text(value)
    return value


def normalize_governance_terms(text: str) -> str:
    for old, new in {
        "轮值 CEO": "轮值董事长/轮值主席",
        "轮值CEO": "轮值董事长/轮值主席",
        "轮值首席执行官": "轮值董事长/轮值主席",
        "Rotating CEO": "Rotating Chair",
        "rotating CEO": "rotating chair",
    }.items():
        text = text.replace(old, new)
    return text


def soften_absolute_discipline(text: str) -> str:
    for pattern in [r"一律(?:予以)?开除", r"直接(?:予以)?开除", r"立即(?:予以)?开除", r"必须(?:予以)?开除", r"开除处理", r"连坐"]:
        text = re.sub(pattern, "可能触发纪律处分、法务介入或劳动关系处理", text)
    return text


def ensure_strategy_labels(text: str) -> str:
    markers = ("[资料事实]", "[合理推断]", "[高不确定预测]")
    if all(marker in text for marker in markers):
        return text
    section = """
## 事实 / 推断 / 预测标注
[资料事实]
- 仅指输入资料、上传文档或明确来源中已经出现的信息。

[合理推断]
- 指基于公司资料、组织结构和行业常识推出的较高概率判断，需要在实际使用中结合最新情况校准。

[高不确定预测]
- 指未来 12-24 个月可能出现但受外部环境、组织动作、政策与竞争影响较大的判断，不能当作确定事实。
""".strip()
    if "## 诚实边界" in text:
        return text.replace("## 诚实边界", section + "\n\n## 诚实边界", 1)
    return text.rstrip() + "\n\n" + section


def normalize_final_output(file_name: str, text: str) -> str:
    cleaned = sanitize_llm_output(text)
    cleaned = soften_absolute_discipline(normalize_governance_terms(cleaned))
    if file_name == "strategy-vision.md":
        cleaned = ensure_strategy_labels(cleaned)
    return cleaned.strip()


GLOBAL_OUTPUT_RULES = """
### 全局输出硬约束
- 只输出最终正式文档，不输出思考过程、草稿、解释、免责声明。
- 禁止输出 <think>、</think>、reasoning、analysis、chain of thought。
- 不要把输入资料判定为乱码后要求用户重新提供；若资料不足，应基于可用信息明确写“诚实边界”。
- 输出必须围绕目标公司、组织架构、老板 skill、员工集合体 skill、公司行为准则展开，禁止跑题成教程、代码示例或通用知识文章。
- 最终交付文件必须写成可调用 Skill，不要写成“公司深度分析报告”“公司概况”“核心竞争力分析”。
- 涉及收入、利润、研发投入、人数、治理职位等硬事实时，只能使用输入资料中明确出现的数据；不能确定就写入诚实边界，不要编造。
- API Key、Authorization、Token、Secret 等敏感信息绝不能写入任何输出文件。
- 涉及华为等轮值治理体系时，使用“轮值董事长/轮值主席/Rotating Chair”，不要写成“轮值 CEO”。
- 涉及处分、合规、法务后果时，不要写“一律开除/直接开除/连坐”等绝对结论；改写为“可能触发纪律处分、法务介入或劳动关系处理”。
- strategy-vision.md 必须显式区分 [资料事实]、[合理推断]、[高不确定预测]。
- 所有最终 Skill 必须内置“低废话表达协议”：默认先结论、后必要依据、再最小动作；只有用户明确要求展开时才输出完整版。
- 抽象词必须落地：使用“赋能、协同、闭环、抓手、长期主义、数据驱动、打造标杆、形成合力”等词时，必须绑定具体机制、动作、指标或约束；否则删除。
- 合理冲突可以保留，不要强行写成“统筹兼顾、加强协同”；应直接说明冲突点、优先约束和最小可行动作。
- 避免无信息边界句，例如“需要结合实际情况综合判断”；除非同时说明缺什么信息、谁来确认、下一步怎么补。
- 第一句必须尽量直接回答问题；除非用户询问背景，不要用“在当前复杂多变的环境下”“随着行业竞争加剧”等背景句开头。
- 当三层判断冲突时，不要写“统筹兼顾、加强协同”；必须给出冲突点、优先约束和当前最小动作。
""".strip()


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

LEGACY_LONG_DEFAULT_PHRASES = [
    "默认保留完整版",
    "完整版：默认模式",
    "默认模式，保留分层分析",
    "普通问题默认输出完整分层",
]


BAD_OUTPUT_INDICATORS = (
    "The text you pasted appears",
    "appears to be a long",
    "garbled",
    "jumbled",
    "corrupted",
    "encoded text",
    "无法理解",
    "看起来像乱码",
    "像乱码",
    "乱码",
    "加密文本",
    "请提供更多上下文",
    "请重新提供",
    "please provide",
    "Flask app",
    "Flask 应用",
    "Flask 入门",
)



FILE_CONTRACTS: dict[str, dict[str, Any]] = {
    "boss-skill.md": {
        "title": "# {company_name} · 老板 Skill",
        "sections": ["## 定位", "## 身份锚点", "## 核心心智模型", "## 决策启发式", "## 优先级排序器", "## 表达 DNA", "## 反模式", "## 典型回答框架", "## 诚实边界"],
        "purpose": "提炼最高决策层的认知操作系统，回答战略、取舍、优先级和资源配置问题。",
        "forbidden": ["深度分析报告", "公司分析报告", "公司概况", "财务表现", "组织架构与核心竞争力分析"],
    },
    "employee-collective-skill.md": {
        "title": "# {company_name} · 员工集合体 Skill",
        "sections": ["## 定位", "## 适用问题", "## 组织默认工作流", "### 场景 1：新需求进入", "### 场景 2：需求评审", "### 场景 3：推进中遇阻", "### 场景 4：上线 / 交付 / 复盘", "## 组织共识", "## 典型分歧点", "## 表达风格", "## 典型摩擦点", "## 执行启发式", "## 典型回答框架", "## 诚实边界"],
        "purpose": "提炼员工群体的现实推进方式，回答 owner、流程、协作、摩擦和落地路径问题。",
        "forbidden": ["深度分析报告", "公司分析报告", "老板技能", "管理层视角", "财务表现"],
    },
    "company-code.md": {
        "title": "# {company_name} · 公司行为准则",
        "sections": ["## 定位", "## 规则优先级", "## 一级红线", "## 决策权限", "## 沟通准则", "## 文档准则", "## 会议准则", "## 升级机制", "## 反馈准则", "## 绩效 / 交付最低要求", "## 规则回答框架", "## 诚实边界"],
        "purpose": "提炼组织级硬规则，回答允许/禁止、审批、升级、留痕、越权和合规边界问题。",
        "forbidden": ["深度分析报告", "公司分析报告", "组织架构与核心竞争力分析", "核心竞争力分析", "公司概况"],
    },
    "SKILL.md": {
        "title": "# {company_name}.skill",
        "sections": ["## 你是什么", "## 由哪三层组成", "## 组织插槽成员规则", "## 回答原则", "## 提示词协议优先级", "## 规则冲突裁决", "## 套话替换词典", "## 模型适配策略", "## Prompt 发布冻结标记", "## 运行时输出合同", "## 回答前压缩门", "## 证据强度与资料边界", "## 资料短链与判断来源", "## 路由规则", "## 回答输出模式", "## 标准输出格式", "## 运行时表达案例"],
        "required_markers": ["[问题归类]", "[组织插槽成员识别]", "[老板视角]", "[员工集合体视角]", "[组织插槽成员视角]", "[行为准则校验]", "[综合建议]", "[组织落地建议]", "[发言人简易版]", "[诚实边界]"],
        "purpose": "把老板 skill、员工集合体 skill、公司行为准则组装成可调用的总入口。",
        "forbidden": ["深度分析报告", "公司分析报告", "公司概况", "核心竞争力", "财务表现"],
    },
    "strategy-vision.md": {
        "title": "# {company_name} · 未来规划战略图景",
        "sections": ["## 战略前提", "## 未来 12-24 个月总目标", "## 三阶段推进节奏", "### 第一阶段（0-6个月）", "### 第二阶段（6-12个月）", "### 第三阶段（12-24个月）", "## 组织资源配置建议", "## 可能出现的三类分歧", "## 给管理层的建议", "## 事实 / 推断 / 预测标注", "## 诚实边界"],
        "required_markers": ["[资料事实]", "[合理推断]", "[高不确定预测]"],
        "purpose": "基于三层 skill 输出未来 12-24 个月的现实战略图景。",
        "forbidden": ["深度分析报告", "公司分析报告", "鸡汤", "愿景口号"],
    },
    "career-path.md": {
        "title": "# {position_name} · 在 {company_name} 的未来职业发展预测",
        "sections": ["## 当前岗位定位", "## 未来 12-24 个月的发展主线", "## 三阶段成长路径", "### 第一阶段（0-6个月）", "### 第二阶段（6-12个月）", "### 第三阶段（12-24个月）", "## 对上、对内、对外的能力要求", "## 最值得补的 5 项能力", "## 风险预警", "## 给个人的行动建议", "## 诚实边界"],
        "purpose": "基于组织人员资料与同组织模型，预测目标岗位/人员未来发展路径。",
        "forbidden": ["深度分析报告", "公司分析报告", "公司概况"],
    },
}

REPORT_STYLE_PATTERNS = (
    r"^#\s*.+(?:深度分析报告|公司分析报告|综合分析报告|核心竞争力分析|组织架构与核心竞争力分析)",
    r"^#\s*.+(?:概况|简介)$",
)


def _contract_text(file_name: str, company_name: str, position_name: str = "目标岗位") -> str:
    contract = FILE_CONTRACTS[file_name]
    title = contract["title"].format(company_name=company_name, position_name=position_name)
    section_lines = "\n".join(f"- {section}" for section in contract["sections"])
    marker_lines = "\n".join(f"- {marker}" for marker in contract.get("required_markers", []))
    marker_block = f"\n\n必须包含以下运行时标记，名称必须原样出现，但不得当作独立章节反复展开：\n{marker_lines}" if marker_lines else ""
    forbidden_lines = "、".join(contract.get("forbidden", []))
    return f"""
### 当前文件契约：{file_name}
目标：{contract['purpose']}

必须使用的标题：
{title}

必须包含以下章节，名称必须原样出现，不得改名：
{section_lines}{marker_block}

内容要求：
- 这是 Skill 文件，不是公司介绍、研究报告、新闻稿、教程或普通分析文章。
- 必须写成可调用的角色/规则/路由能力，能直接回答用户问题。
- 资料不足处写入“诚实边界”，不得编造确定事实。
- 涉及收入、利润、研发投入、人数、治理职位等硬事实时，只能使用输入资料中明确出现的数据；若资料冲突，写明“资料存在冲突，需以最新官方资料核验”。
- 禁止输出这些报告式标题或内容：{forbidden_lines}
- 不得泄露 API Key、Authorization、Token、Secret 等敏感信息。
- 不得使用“轮值 CEO”作为华为类轮值治理体系的称谓，应写“轮值董事长/轮值主席/Rotating Chair”。
- 处分和合规后果必须写成风险等级，不得写“一律开除”“直接开除”“连坐”等无法公开核验的绝对结论。
- 如果当前文件是 strategy-vision.md，必须用 [资料事实]、[合理推断]、[高不确定预测] 标注事实和推演边界。
- 关键判断必须区分强依据、弱依据、推断、模板默认和未知；不得把推断伪装成事实，不得把模板默认写成公司制度。
- 提示词协议优先级必须裁决短答、证据、冲突和结构之间的冲突；运行时回答优先短、准、可执行。
- 模型适配不得削弱安全、资料边界和规则红线；本地/弱指令模型只减少展开，不能降低可信表达要求。
- Prompt 发布冻结标记必须说明：后续只能做有目的的修复、去重、检查补强或模型适配修正，不得无理由继续堆叠运行时协议。
""".strip()


def append_file_contract(prompt: str, file_name: str, company_name: str, position_name: str = "目标岗位") -> str:
    if file_name not in FILE_CONTRACTS:
        return prompt
    return f"{prompt}\n\n{_contract_text(file_name, company_name, position_name)}"


def find_contract_problems(file_name: str, text: str, company_name: str = "", position_name: str = "") -> list[str]:
    cleaned = sanitize_llm_output(text)
    problems: list[str] = []
    if file_name not in FILE_CONTRACTS:
        return problems
    if len(cleaned) < 180:
        problems.append("内容过短，无法作为完整 Skill 文件")
    if any(marker in cleaned for marker in ("PK\x03\x04", "[Content_Types].xml", "word/document.xml")):
        problems.append("包含 Office/ZIP 二进制残留")
    if "<think" in cleaned.lower() or "</think" in cleaned.lower():
        problems.append("包含 think 标签")

    contract = FILE_CONTRACTS[file_name]
    expected_title = contract["title"].format(company_name=company_name or "", position_name=position_name or "目标岗位")
    first_heading = next((line.strip() for line in cleaned.splitlines() if line.strip().startswith("# ")), "")
    if expected_title and first_heading and first_heading != expected_title:
        if not (file_name == "SKILL.md" and first_heading.replace(" ", "") == expected_title.replace(" ", "")):
            problems.append(f"一级标题不符合文件契约，应为：{expected_title}")
    elif expected_title and not first_heading:
        problems.append(f"缺少一级标题：{expected_title}")

    missing_sections = [section for section in contract["sections"] if section not in cleaned]
    if missing_sections:
        problems.append("缺少必需章节：" + "、".join(missing_sections[:8]))

    missing_markers = [marker for marker in contract.get("required_markers", []) if marker not in cleaned]
    if missing_markers:
        problems.append("缺少必需运行时标记：" + "、".join(missing_markers[:8]))

    if file_name == "SKILL.md":
        legacy_hits = [phrase for phrase in LEGACY_LONG_DEFAULT_PHRASES if phrase in cleaned]
        if legacy_hits:
            problems.append("残留旧版默认长答规则：" + "、".join(legacy_hits[:4]))

        stripped_lines = [line.strip() for line in cleaned.splitlines()]
        duplicate_markers = [marker for marker in RUNTIME_REPORT_MARKERS if stripped_lines.count(marker) > 1]
        if duplicate_markers:
            problems.append("运行时标记重复：" + "、".join(duplicate_markers[:8]))

        runtime_required = [
            "结论版（默认）", "决策卡", "普通问题禁止输出报告版标记", "冲突卡", "缺口卡",
            "资料短链", "判断来源", "回答前压缩门", "背景式开头", "最小动作", "自检过程",
            "模型适配", "强指令模型", "通用云端模型", "本地", "弱指令模型", "推理型模型",
            "[发言人简易版]", "不列步骤", "不写完整角色拆解",
        ]
        missing_runtime_rules = [phrase for phrase in runtime_required if phrase not in cleaned]
        if missing_runtime_rules:
            problems.append("缺少运行时短答/证据/压缩/模型适配契约：" + "、".join(missing_runtime_rules[:8]))

    for pattern in REPORT_STYLE_PATTERNS:
        if re.search(pattern, cleaned, flags=re.M):
            problems.append("疑似生成成普通公司分析报告，而不是 Skill 文件")
            break

    forbidden_hits = [item for item in contract.get("forbidden", []) if item and item in cleaned]
    if forbidden_hits:
        problems.append("出现禁用的报告式表达：" + "、".join(forbidden_hits[:5]))

    indicator_hits = [item for item in BAD_OUTPUT_INDICATORS if item.lower() in cleaned.lower()]
    if len(indicator_hits) >= 2:
        problems.append("疑似跑题或把资料当作乱码处理：" + "、".join(indicator_hits[:3]))

    return problems


def build_contract_repair_prompt(file_name: str, draft_text: str, company_name: str, context: str, position_name: str = "目标岗位") -> str:
    problems = find_contract_problems(file_name, draft_text, company_name, position_name)
    problem_text = "\n".join("- " + item for item in problems) if problems else "- 结构基本可用，但仍需按契约重写得更像 Skill 文件"
    return f"""
你是“公司 Skill 文件严格重写器”。

任务：把下面这份【原始输出】重写成合格的 `{file_name}`。

必须遵守：
{_contract_text(file_name, company_name, position_name)}

本次检测到的问题：
{problem_text}

重写规则：
1. 只输出 `{file_name}` 的最终正文，不要解释你做了什么。
2. 不要保留“深度分析报告”“公司分析报告”“公司概况”等报告式结构。
3. 不要把三层职责写串：老板层管战略取舍；员工集合体管现实推进；行为准则管权限、升级、红线与留痕；总入口管路由。
4. 可以使用原始输出中的有效事实，但必须把它改写成可调用 Skill 规则。
5. 事实数字不得乱编；没有把握就写入诚实边界。
6. 禁止输出 <think>、推理过程、草稿、代码块。

【公司背景与资料摘要】
{context[:16000]}

【原始输出】
{sanitize_llm_output(draft_text)[:12000]}
""".strip()


def _fallback_lines(file_name: str, section: str, company_name: str, position_name: str = "目标岗位") -> list[str]:
    """Return safe template content for a missing contract section."""
    if file_name == "employee-collective-skill.md":
        mapping = {
            "## 定位": [f"- 代表 {company_name} 员工群体在日常协作中的默认推进方式。", "- 重点回答 owner、流程、协作、摩擦、落地路径和风险同步。"],
            "## 适用问题": ["- 新需求如何进入流程。", "- 需求评审要准备什么。", "- 跨部门推进遇阻怎么办。", "- 上线、交付、复盘如何闭环。"],
            "## 组织默认工作流": ["- 默认先明确目标、owner、影响范围、资源和验收标准。", "- 跨部门事项必须留痕，并在风险扩大前升级。"],
            "### 场景 1：新需求进入": ["- 先收集背景、目标、收益、影响范围和期望时间。", "- 初步判断是否需要立项、评审或直接进入小范围试点。"],
            "### 场景 2：需求评审": ["- 重点检查目标清晰度、资源占用、依赖关系、风险和验收标准。", "- 无 owner、无边界、无验收标准的需求应退回补充。"],
            "### 场景 3：推进中遇阻": ["- 先定位阻塞类型：资源、权限、依赖、标准、风险或信息缺口。", "- 私下对齐无效时，带着事实、影响和需要裁决点升级。"],
            "### 场景 4：上线 / 交付 / 复盘": ["- 上线前确认验收、回滚、通知、风险和责任人。", "- 复盘关注事实链、根因、机制改进和后续 owner。"],
            "## 组织共识": ["- 结果要可交付，责任要可追踪。", "- 风险要前置，跨部门承诺要留痕。", "- 讨论可以开放，交付必须闭环。"],
            "## 典型分歧点": ["- 速度与质量的分歧：按风险等级和客户影响裁决。", "- 局部目标与整体目标的分歧：按更高层目标裁决。", "- 标准化与灵活处理的分歧：按复用价值和风险可控性裁决。"],
            "## 表达风格": ["- 结论先行，说明依据、影响和需要的支持。", "- 少用空泛表态，多给事实、owner、deadline 和风险。"],
            "## 典型摩擦点": ["- 需求边界不清导致反复返工。", "- owner 不清导致无人负责。", "- 风险晚暴露导致交付事故。", "- 跨部门目标不一致导致推进缓慢。"],
            "## 执行启发式": ["- 任何跨部门事项先定唯一 owner。", "- 承诺前确认资源和依赖。", "- 风险影响 deadline 时立即升级。", "- 评审前写清验收标准。", "- 复盘只接受事实链和改进行动。"],
            "## 典型回答框架": ["1. 判断属于哪个组织场景。", "2. 明确默认 owner。", "3. 给出推进路径。", "4. 标出最可能卡住的点。", "5. 给出避坑动作。", "6. 必要时触发行为准则校验。"],
            "## 诚实边界": ["- 不同部门和团队可能存在执行差异。", "- 缺少内部资料时，只能给出高概率组织行为推断。", "- 具体权限、绩效和人事问题需以公司真实制度为准。"],
        }
    elif file_name == "boss-skill.md":
        mapping = {
            "## 定位": [f"- 代表 {company_name} 最高决策层的战略判断方式。", "- 重点回答方向、取舍、资源配置和风险边界。"],
            "## 身份锚点": ["- 以公司长期主义、主航道和组织韧性作为身份锚点。", "- 不代替真实管理层发言，不编造内部信息。"],
            "## 核心心智模型": ["- 先识别一阶目标，再判断约束、风险和资源。", "- 对不确定事实保持审慎，优先要求证据。"],
            "## 决策启发式": ["- 判断是否属于核心主航道。", "- 明确 owner、风险、资源和交付标准后再推进。", "- 信息不足时先缩小试点，不做确定承诺。"],
            "## 优先级排序器": ["- 默认优先级：合规安全、战略窗口、客户价值、质量、组织可控性、效率。", "- 当风险升高时，风险和质量优先于速度。"],
            "## 表达 DNA": ["- 结论先行，证据支撑，风险前置。", "- 语言直接、克制，不把推断写成事实。"],
            "## 反模式": ["- 反对只有口号没有 owner 的方案。", "- 反对只有增长愿望而无资源约束的方案。"],
            "## 典型回答框架": ["1. 判断问题类型。", "2. 找到一阶目标。", "3. 识别关键约束。", "4. 给出取舍和下一步动作。", "5. 标注诚实边界。"],
            "## 诚实边界": ["- 公开资料之外的内部决策不能强行断言。", "- 数字、职务和治理信息需以最新官方资料核验。"],
        }
    elif file_name == "company-code.md":
        mapping = {
            "## 定位": [f"- 用于判断 {company_name} 语境下允许、禁止、审批、升级和留痕边界。"],
            "## 规则优先级": ["1. 法律、合规、安全。", "2. 公司一级红线。", "3. 业务目标。", "4. 组织流程。", "5. 团队习惯和个人偏好。"],
            "## 一级红线": ["- 不编造事实或夸大承诺。", "- 不绕过关键审批。", "- 不隐瞒重大风险。", "- 不把个人判断包装成公司规则。"],
            "## 决策权限": ["- 常规事项由直接 owner 判断。", "- 跨部门资源、预算、合同、对外承诺需升级或会签。"],
            "## 沟通准则": ["- 承诺、变更、风险必须书面化。", "- 汇报应包含结论、依据、影响、风险和需要支持。"],
            "## 文档准则": ["- 需求、评审、上线、复盘和对外承诺应保留文档。", "- 文档至少包含目标、范围、owner、时间、风险和验收标准。"],
            "## 会议准则": ["- 会前有议题，会中有裁决，会后有 owner 和 deadline。"],
            "## 升级机制": ["- 影响交付、合规、客户或跨部门资源时升级。", "- 升级材料应包含事实、影响、已尝试动作和需要裁决点。"],
            "## 反馈准则": ["- 反馈基于事实、影响和建议，不攻击个人。"],
            "## 绩效 / 交付最低要求": ["- 交付可验收、风险可追踪、责任可闭环。"],
            "## 规则回答框架": ["1. 判断是否规则问题。", "2. 找到规则层级。", "3. 给出允许/禁止动作。", "4. 规则冲突时按优先级裁决。", "5. 信息不足时写明诚实边界。"],
            "## 诚实边界": ["- 未在输入资料中出现的制度不能当作确定规则。", "- 处分和合规后果只能写为风险等级。"],
        }
    elif file_name == "SKILL.md":
        mapping = {
            "## 你是什么": [f"- 这是 {company_name} 的公司三层路由 Skill，总入口负责分派用户问题。"],
            "## 由哪三层组成": ["- 老板 Skill：处理战略、取舍、资源配置。", "- 员工集合体 Skill：处理流程、协作、执行推进。", "- 公司行为准则：处理权限、红线、升级、合规与留痕。", "- 组织插槽成员：处理用户显式插入的员工资料、员工 Skill 或外部来源 Skill 成员。"],
            "## 组织插槽成员规则": ["- 插槽成员是非覆盖式叠加，不替换原老板 Skill、员工集合体 Skill 或公司行为准则。", "- 用户点名某成员时，先匹配 organization-members.md 中的成员名称、Skill 识别名和别名。", "- 插入老板位、董事会/创始层或高管层的成员，应作为插槽领导成员回答，而不是只作为外部顾问。", "- 多名成员在同一位置时，应并列比较建议，再由 company-code.md 校验边界。"],
            "## 回答原则": ["- 先归类问题，再调用相应层级。", "- 事实和推断分开写。", "- 不确定处写入诚实边界。", "- 默认使用结论版/决策卡，不自动展开完整分层报告。", "- 只有用户明确要求详细、完整、报告或分层拆解时，才输出报告版。", "- 用户要求简易版、发言人版、对外口径或简短总结时，改用发言人简易版。"],
            "## 提示词协议优先级": ["1. 安全与保密优先。", "2. 资料边界优先。", "3. 规则红线优先。", "4. 用户意图优先。", "5. 运行时输出合同优先。", "6. 表达压缩优先。", "7. 文件结构最后。"],
            "## 规则冲突裁决": ["- 短答与证据说明冲突时，短答优先，只标关键证据；用户追问再展开来源短链。", "- 冲突裁决与低废话冲突时，只写冲突点、优先级和最小动作。", "- 用户要求确定结论但资料不足时，资料边界优先，进入缺口卡。", "- 对外口径不暴露内部推断和资料短链。", "- 老板要快但规则不允许时，规则红线优先；可拆成内部灰度、补审批、再对外。"],
            "## 套话替换词典": ["- 抽象词必须绑定 owner、节点、审批、指标、材料、时间或边界。", "- 加强协同要写成谁和谁在什么节点对齐什么材料。", "- 形成闭环要写成输入、处理、验收、复盘分别是什么。", "- 数据驱动要写成当前基线、目标指标和复盘周期。", "- 赋能组织要写成工具、权限、模板、训练或资源。"],
            "## 模型适配策略": ["- 强指令模型可保留完整协议，但仍默认短答。", "- 通用云端模型优先执行决策卡、冲突卡、口径卡、缺口卡。", "- 本地/弱指令模型只保留第一句结论、禁止背景开头、最小动作和资料缺口。", "- 推理型模型不得输出 chain of thought、analysis、reasoning、思考过程或自检过程。", "- 模型适配只能减少展开，不能削弱安全、资料边界和规则红线。"],
            "## Prompt 发布冻结标记": ["- 当前提示词协议已进入 v0.1.0 正式版冻结状态。", "- 后续修改必须说明目的、优先级、影响范围和质量检查覆盖。", "- 不得删除运行时输出合同、回答前压缩门、证据强度、资料短链、模型适配和套话替换词典。", "- 默认运行时输出仍以短答为主，不因发布冻结回到报告版。"],
            "## 运行时输出合同": ["- 决策卡：普通问题默认输出 结论 / 依据 / 动作 / 边界。", "- 冲突卡：战略、执行、规则冲突时输出 结论 / 冲突点 / 优先级 / 最小动作。", "- 口径卡：官方、对外、发言人回复只输出 [发言人简易版] 和 1-2 段统一口径。", "- 缺口卡：资料不足时输出 不能确定 / 能确定 / 补充，不写长免责声明。"],
            "## 回答前压缩门": ["- 输出前先在内部删除背景式开头、重复原则、套话和无动作免责声明。", "- 抽象词必须绑定 owner、节点、审批、指标、材料、时间或边界；否则删除或改写。", "- 没有最小动作时补动作；资料不足时改用缺口卡。", "- 不向用户展示压缩门、自检过程或格式选择过程，只输出最终答案。"],
            "## 证据强度与资料边界": ["- 强依据：资料或用户明确说明直接支持，可以作为判断依据。", "- 弱依据：多个片段共同暗示，必须谨慎表达。", "- 推断：基于组织逻辑、岗位职责或行业经验形成，不得写成事实。", "- 模板默认：来自模板或通用组织经验，只能作为建议，不能写成公司制度。", "- 未知：资料不足时进入缺口卡。", "- 普通回答不必逐句贴标签，但关键判断、冲突裁决、权限边界、风险判断和人员发展判断必须体现证据强度。"],
            "## 资料短链与判断来源": ["- 关键判断在内部必须能追溯到上传资料、用户说明、组织人员资料、模板默认或推断。", "- 默认运行时回答不展开长引用；用户追问依据时，最多输出 3 条来源短链。", "- 短链格式：判断 / 来源短链 / 说明。", "- 不把模板默认、推断或弱依据写成公司事实、老板明确要求或硬制度。", "- 多来源冲突时进入冲突卡，说明冲突来源、优先级和最小动作。"],
            "## 路由规则": ["- 战略类问题走老板视角。", "- 执行类问题走员工集合体视角。", "- 规则类问题走行为准则。", "- 混合类问题三层合并输出。"],
            "## 回答输出模式": ["- 结论版（默认）：普通问题优先用决策卡，通常 1 段或 3-5 条要点。", "- 分析版（按需）：用户要求分析、原因或推进方案时，输出结论、关键原因、冲突/风险、动作建议和短边界。", "- 报告版（仅明确要求时）：用户明确要求完整、详细、报告或分层拆解时，才保留问题归类、老板视角、员工集合体视角、组织插槽成员视角、行为准则校验、综合建议、组织落地建议和诚实边界。", "- 普通问题禁止输出报告版标记：[问题归类]、[老板视角]、[员工集合体视角]、[行为准则校验]、[综合建议]、[诚实边界]；这些标记只在报告版中使用。", "- 简易模式：当用户要求‘简易版’‘简短回答’‘发言人形式’‘对外口径’‘官方口径’‘给外部看的回答’‘只要结论’时，内部参考各层判断，但只输出 [发言人简易版] 的最终结论。", "- 简易模式必须极简：通常 1 段，最多 2 段；不列步骤、不写完整角色拆解、不写长边界说明；但要保留被点名组织插槽成员/员工 Skill 的语气特征。"],
            "## 标准输出格式": ["- 普通问题默认走决策卡：结论 / 依据 / 动作 / 边界。", "- 冲突问题默认走冲突卡：结论 / 冲突点 / 优先级 / 最小动作。", "- 资料不足默认走缺口卡：不能确定 / 能确定 / 补充。", "- 用户明确要求报告版时，才使用以下完整分层标记：", "[问题归类]", "[组织插槽成员识别]", "[老板视角]", "[员工集合体视角]", "[组织插槽成员视角]", "[行为准则校验]", "[综合建议]", "[组织落地建议]", "[发言人简易版]", "[诚实边界]"],
            "## 运行时表达案例": ["- 普通判断合格表达：可以推进，但先限定范围和 owner。", "- 冲突裁决合格表达：可以推进，但合规优先；先内部灰度，再补审批。", "- 发言人口径合格表达：只给 1 段对外结论，不暴露内部拆解。", "- 资料不足合格表达：不能确定必然结果；能确定当前资料支持什么；补充哪些信息会改变判断。", "- 不合格表达：统筹兼顾、加强协同、形成闭环，但没有 owner、节点、审批、指标、时间或边界。"],
            "[问题归类]": ["- 说明问题属于战略、执行、规则、组织插槽成员或混合类型。"],
            "[组织插槽成员识别]": ["- 如用户点名插入成员，说明匹配到的成员、插入层级、岗位、权限级别和非覆盖关系。"],
            "[老板视角]": ["- 给出方向、取舍和资源配置判断。"],
            "[员工集合体视角]": ["- 给出 owner、流程、阻塞点和推进路径。"],
            "[组织插槽成员视角]": ["- 如适用，按插入位置和权限给出该成员的领导动作、协作建议或执行视角。"],
            "[行为准则校验]": ["- 检查是否触发权限、红线、升级或留痕要求。"],
            "[综合建议]": ["- 汇总成可执行下一步。"],
            "[组织落地建议]": ["- 对非代码问题给出组织执行路线、责任人、指标和验证方式。"],
            "[发言人简易版]": ["- 用 1 段发言人式最终结论综合完整版判断；最多 2 段。", "- 不使用内部推理小标题，不逐项展开角色链路，不列长清单。", "- 如涉及外部/插槽成员，只用一句短边界说明；不要展开免责声明。"],
            "[诚实边界]": ["- 标出资料不足和需要核验的内容。"],
        }
    elif file_name == "strategy-vision.md":
        mapping = {
            "## 战略前提": ["[资料事实] 基于上传资料中明确出现的信息。", "[合理推断] 结合组织结构和业务语境进行判断。"],
            "## 未来 12-24 个月总目标": ["[合理推断] 聚焦主航道、提升组织可控性、降低关键风险。"],
            "## 三阶段推进节奏": ["- 以 0-6 个月、6-12 个月、12-24 个月分阶段推进。"],
            "### 第一阶段（0-6个月）": ["- 梳理重点项目、owner、风险和资源缺口。"],
            "### 第二阶段（6-12个月）": ["- 放大已验证方向，淘汰低价值消耗。"],
            "### 第三阶段（12-24个月）": ["- 固化组织机制，形成可复制能力。"],
            "## 组织资源配置建议": ["- 资源优先投向战略窗口期、客户价值和高确定性能力建设。"],
            "## 可能出现的三类分歧": ["- 速度与质量。", "- 短期交付与长期能力。", "- 局部指标与整体目标。"],
            "## 给管理层的建议": ["- 用事实、风险和资源约束来裁决分歧。"],
            "## 事实 / 推断 / 预测标注": ["[资料事实] 输入资料明确出现的信息。", "[合理推断] 基于资料的高概率判断。", "[高不确定预测] 未来可能发生但需要持续校准的判断。"],
            "[资料事实]": ["- 仅写输入资料中明确出现的信息。"],
            "[合理推断]": ["- 基于资料和组织逻辑形成的判断。"],
            "[高不确定预测]": ["- 受市场、政策、竞争和组织执行影响较大的推演。"],
            "## 诚实边界": ["- 未来规划存在不确定性，不能当作确定事实。"],
        }
    elif file_name == "career-path.md":
        mapping = {
            "## 当前岗位定位": [f"- {position_name} 在 {company_name} 中承担具体业务、技术或协作责任。"],
            "## 未来 12-24 个月的发展主线": ["- 从完成任务走向承担 owner、协同和风险前置。"],
            "## 三阶段成长路径": ["- 按 0-6 个月、6-12 个月、12-24 个月推进。"],
            "### 第一阶段（0-6个月）": ["- 补齐岗位基本能力，建立稳定交付记录。"],
            "### 第二阶段（6-12个月）": ["- 承担跨角色协作，提升问题拆解和复盘能力。"],
            "### 第三阶段（12-24个月）": ["- 形成可复用方法，承担更复杂 owner 角色。"],
            "## 对上、对内、对外的能力要求": ["- 对上结论清楚，对内协作顺畅，对外口径稳定。"],
            "## 最值得补的 5 项能力": ["1. 业务理解。", "2. 结构化表达。", "3. 项目 owner 能力。", "4. 风险前置。", "5. 复盘沉淀。"],
            "## 风险预警": ["- 只做执行不理解目标。", "- 不暴露风险。", "- 缺少文档和复盘。"],
            "## 给个人的行动建议": ["- 主动对齐目标，保留过程证据，按阶段提升 owner 能力。"],
            "## 诚实边界": ["- 个人发展预测受岗位、团队和业务变化影响，需要持续校准。"],
        }
    else:
        mapping = {}
    return mapping.get(section, ["- 该章节由程序兜底补齐；请结合已上传资料继续细化。", "- 资料不足处必须写入诚实边界，不得编造确定事实。"] )


def _extract_section(text: str, section: str) -> str:
    lines = text.splitlines()
    target_level = len(section) - len(section.lstrip('#')) if section.startswith('#') else 0
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == section:
            start = idx + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith('#'):
            # Contract repair loops through parent headings and child headings
            # separately. Stop at any subsequent heading to avoid swallowing
            # nested sections and duplicating them later in coerce_output_contract.
            break
        if target_level == 0 and stripped:
            # Plain runtime markers such as [老板视角] are validated as
            # required_markers and should not be treated as long sections.
            # Stop before the next plain marker when extracting marker bodies.
            if stripped.startswith('[') and stripped.endswith(']'):
                break
        body.append(line)
    return "\n".join(body).strip()

def coerce_output_contract(file_name: str, draft_text: str, company_name: str, context: str, position_name: str = "目标岗位") -> str:
    """Programmatically repair a generated document into the required contract skeleton."""
    if file_name not in FILE_CONTRACTS:
        return normalize_final_output(file_name, draft_text)
    cleaned = normalize_final_output(file_name, draft_text)
    contract = FILE_CONTRACTS[file_name]
    title = contract["title"].format(company_name=company_name or "公司", position_name=position_name or "目标岗位")
    blocks = [title, ""]
    force_fallback_sections = set()
    if file_name == "SKILL.md":
        force_fallback_sections = {
            "## 回答原则",
            "## 模型适配策略",
            "## Prompt 发布冻结标记",
            "## 运行时输出合同",
            "## 回答前压缩门",
            "## 证据强度与资料边界",
            "## 回答输出模式",
            "## 标准输出格式",
            "## 运行时表达案例",
        }
    elif file_name == "strategy-vision.md":
        force_fallback_sections = {"## 事实 / 推断 / 预测标注"}
    for section in contract["sections"]:
        blocks.append(section)
        body = "" if section in force_fallback_sections else _extract_section(cleaned, section)
        if not body or len(body) < 12:
            body = "\n".join(_fallback_lines(file_name, section, company_name, position_name))
        blocks.append(body.strip())
        blocks.append("")
    repaired = "\n".join(blocks)
    return normalize_final_output(file_name, repaired)


def _only_nonfatal_contract_problems(problems: list[str]) -> bool:
    fatal_markers = ("Office/ZIP 二进制残留", "think 标签", "内容过短", "跑题或把资料当作乱码处理")
    return not any(any(marker in problem for marker in fatal_markers) for problem in problems)


def ensure_output_contract(file_name: str, draft_text: str, company_name: str, context: str, settings: dict[str, Any], position_name: str = "目标岗位") -> str:
    """Validate and repair final Skill markdown without doing a second LLM rewrite.

    Earlier versions called the model again when a generated file missed fixed
    headings. On slow local 32B models this could add tens of minutes and could
    are repaired by code immediately; only fatal content problems still stop the
    run.
    """
    cleaned = normalize_final_output(file_name, draft_text)
    problems = find_contract_problems(file_name, cleaned, company_name, position_name)
    if not problems:
        return cleaned

    if not _only_nonfatal_contract_problems(problems):
        raise RuntimeError(f"{file_name} 存在致命输出问题，已阻止写入：" + "；".join(problems))

    logger.warning("%s 未通过结构契约校验，跳过模型自动重写，直接进行程序模板兜底修复：%s", file_name, "；".join(problems))
    coerced = coerce_output_contract(file_name, cleaned, company_name, context, position_name)
    final_remaining = find_contract_problems(file_name, coerced, company_name, position_name)

    if final_remaining and not _only_nonfatal_contract_problems(final_remaining):
        raise RuntimeError(f"{file_name} 程序模板兜底后仍存在致命输出问题：" + "；".join(final_remaining))
    if final_remaining:
        logger.warning("%s 程序模板兜底后仍存在非致命结构提醒，允许继续写入：%s", file_name, "；".join(final_remaining))
    else:
        logger.info("%s 已通过程序模板兜底修复并通过结构契约校验", file_name)
    return coerced

def build_cacheable_common_prefix(company_name: str, context: str) -> str:
    """Build a stable public prefix shared by all LLM calls in one generation run.

    DeepSeek context caching works best when repeated content appears as an
    identical prefix. Keep company materials, organization, summaries and global
    constraints before any module-specific instruction. Do not include module
    names, file names, timestamps, generated intermediate outputs or other
    per-call data in this block.
    """
    safe_company_name = str(company_name or "目标公司").strip() or "目标公司"
    safe_context = str(context or "").strip()
    return f"""### 公司 Skill 生成公共上下文（缓存友好固定前缀）
以下公共前缀会在本轮所有 LLM 请求中保持相同顺序和相同内容。
请把它视为所有模块共用的事实底座、组织底座和输出边界。
模块任务、文件名、阶段性产物会放在该公共前缀之后，避免破坏前缀复用。

### 目标公司
{safe_company_name}

### 统一公司资料与生成上下文
{safe_context}

### 公共输出硬约束
{GLOBAL_OUTPUT_RULES}

### 公共上下文结束
""".strip()


def build_module_prompt(common_prefix: str, module_name: str, module_template: str, module_body: str = "") -> str:
    """Append module-specific instructions after the shared cacheable prefix."""
    body = str(module_body or "").strip()
    template = str(module_template or "").strip()
    parts = [
        common_prefix.strip(),
        f"### 本次模块任务：{module_name}",
        template,
    ]
    if body:
        parts.append(body)
    return "\n\n".join(part for part in parts if part)


def with_output_rules(prompt: str) -> str:
    # can cache repeated public context from the beginning of each request. Keep
    # this guard for preview/legacy paths that may still call LLM without the
    # shared prefix.
    if GLOBAL_OUTPUT_RULES in prompt:
        return prompt
    return f"{prompt}\n\n{GLOBAL_OUTPUT_RULES}"


def sanitize_llm_output(text: str) -> str:
    """Remove local reasoning tags and normalize model output before writing files."""
    text = "" if text is None else str(text)
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.I | re.S)
    text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"^\s*```(?:markdown|md|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    text = mask_secret_text(text)
    return text.strip()


def assert_materials_are_readable(company_docs_summary: str) -> None:
    """Stop early if an Office file was still parsed as binary or failed completely."""
    summary = company_docs_summary or ""
    bad_markers = ("PK\x03\x04", "[Content_Types].xml", "word/document.xml")
    if any(marker in summary for marker in bad_markers):
        raise RuntimeError(
            "资料解析失败：检测到 DOCX/Office 二进制内容进入资料摘要。"
            "请使用修复版重新上传文件，或运行 pip install -r requirements.txt 后重试。"
        )
    if "[DOCX解析失败]" in summary:
        raise RuntimeError(
            "资料解析失败：Word 文档没有被成功提取正文。"
            "请确认该 .docx 文件可正常打开，并运行 pip install -r requirements.txt 后重试。"
        )


def validate_generated_outputs(outputs: dict[str, str]) -> None:
    """Catch obviously unusable generations before writing final skill files."""
    problems: list[str] = []
    for name, text in outputs.items():
        cleaned = sanitize_llm_output(text)
        if len(cleaned) < 60:
            problems.append(f"{name}: 内容过短，疑似未正常生成")
        if any(marker in cleaned for marker in ("PK\x03\x04", "[Content_Types].xml", "word/document.xml")):
            problems.append(f"{name}: 包含 Office/ZIP 二进制残留")
        if "<think" in cleaned.lower() or "</think" in cleaned.lower():
            problems.append(f"{name}: 包含 think 标签")
        indicator_hits = [item for item in BAD_OUTPUT_INDICATORS if item.lower() in cleaned.lower()]
        if len(indicator_hits) >= 2:
            problems.append(f"{name}: 疑似跑题或把资料当作乱码处理（{', '.join(indicator_hits[:3])}）")
        if any(pattern.search(cleaned) for pattern in SECRET_VALUE_PATTERNS):
            problems.append(f"{name}: 包含疑似 API Key / Token，已阻止写入")
        if any(term in cleaned for term in ("轮值 CEO", "轮值CEO", "Rotating CEO", "rotating CEO")):
            problems.append(f"{name}: 包含不准确的轮值治理称谓，应使用轮值董事长/轮值主席")
        if name == "company-code.md" and re.search(r"一律(?:予以)?开除|直接(?:予以)?开除|立即(?:予以)?开除|必须(?:予以)?开除|开除处理|连坐", cleaned):
            problems.append(f"{name}: 包含过度绝对的处分结论")
        if name == "strategy-vision.md" and not all(marker in cleaned for marker in ("[资料事实]", "[合理推断]", "[高不确定预测]")):
            problems.append(f"{name}: 缺少事实/推断/预测标注")
    if problems:
        raise RuntimeError("生成质量校验失败，已阻止写入最终 skill 文件：\n- " + "\n- ".join(problems))


DEFAULT_ORGANIZATION = {
    "selected_departments": DEFAULT_DEPARTMENTS,
    "custom_departments": [],
    "org_notes": "参考大型公司常见组织架构的完整设置。",
    "use_uploaded_org_chart": False,
    "org_chart_files": [],
}


DEFAULT_PERSONAL = {
    "enabled": False,
    "level": "",
    "department": "",
    "position": "",
    "role_notes": "",
    "has_peer_models": True,
    "file_path": "",
    "file_paths": [],
    "model_file_path": "",
    "model_file_paths": [],
    "department_assignment": {},
}

DEFAULT_ORGANIZATION_MEMBER = {
    "enabled": True,
    "member_name": "",
    "member_type": "组织成员",
    "level": "",
    "department": "",
    "position": "",
    "role_notes": "",
    "has_peer_models": True,
    "profile_file_path": "",
    "profile_file_paths": [],
    "skill_file_path": "",
    "skill_file_paths": [],
    "file_path": "",
    "file_paths": [],
    "model_file_path": "",
    "model_file_paths": [],
    "department_assignment": {},
    "insert_mode": "append_non_destructive",
    "authority_level": "advisory",
}

GENERIC_MEMBER_NAMES = {"", "员工", "顾问", "组织成员", "外部skill成员", "外部 Skill 成员", "老板位成员", "老板", "CEO", "董事长", "总裁", "创始人", "目标岗位", "未命名成员", "SKILL"}


def infer_skill_identity_from_path(skill_path: str | Path | None) -> tuple[str, list[str]]:
    """Infer a human-readable member identity from an imported Skill path.

    This keeps inserted employee/Skill members discoverable by Codex/Claude even
    when the UI field only says a generic position such as “董事长”. It does not
    claim a real employment relationship; it only extracts aliases for routing.
    """
    if not skill_path:
        return "", []
    path = Path(str(skill_path))
    text_source = " ".join(part for part in path.parts[-4:])
    aliases: list[str] = []
    identity = ""

    def add_alias(value: str) -> None:
        value = value.strip()
        if value and value not in aliases:
            aliases.append(value)

    lower_source = text_source.lower()
    if "elon" in lower_source or "musk" in lower_source or "马斯克" in text_source:
        identity = "Elon Musk"
        for value in ("Elon Musk", "Musk", "马斯克", "埃隆·马斯克"):
            add_alias(value)

    try:
        if path.exists() and path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            raw = read_text_with_encoding_fallback(path)[:6000]
            if not identity:
                frontmatter_name = re.search(r"^name\s*:\s*([^\n#]+)", raw, flags=re.I | re.M)
                if frontmatter_name:
                    candidate = frontmatter_name.group(1).strip().strip('"\'')
                    if candidate and candidate.lower() not in {"skill", "default"}:
                        identity = candidate
                        add_alias(candidate)
                if not identity:
                    heading = re.search(r"^#\s+(.+)$", raw, flags=re.M)
                    if heading:
                        candidate = re.sub(r"[·｜|].*$", "", heading.group(1)).strip()
                        if candidate and candidate.lower() not in {"skill", "skll.md"}:
                            identity = candidate
                            add_alias(candidate)
            if "Elon" in raw or "Musk" in raw or "马斯克" in raw:
                identity = identity or "Elon Musk"
                for value in ("Elon Musk", "Musk", "马斯克", "埃隆·马斯克"):
                    add_alias(value)
    except Exception:
        pass

    if not identity:
        parent = path.parent.name if path.name.lower() == "skill.md" else path.stem
        cleaned = re.sub(r"[-_]+", " ", parent).strip()
        if cleaned and cleaned.lower() not in {"skill", "main"}:
            identity = cleaned
            add_alias(cleaned)
    return identity, aliases


def enrich_organization_member_identity(member: dict[str, Any]) -> dict[str, Any]:
    """Add inferred identity/aliases while preserving explicit user fields."""
    skill_path = member.get("skill_file_path") or member.get("model_file_path") or ""
    inferred, aliases = infer_skill_identity_from_path(skill_path)
    explicit = str(member.get("member_name") or "").strip()
    position = str(member.get("position") or "").strip()
    explicit_key = explicit.replace(" ", "").lower()

    if inferred:
        member["skill_identity"] = inferred
        member["member_aliases"] = aliases
        if not explicit or explicit_key in {item.replace(" ", "").lower() for item in GENERIC_MEMBER_NAMES}:
            member["member_name"] = f"{inferred}（{position}）" if position else inferred
    elif aliases:
        member["member_aliases"] = aliases
    return member


def member_authority_description(authority_level: str | None) -> str:
    value = str(authority_level or "advisory").strip().lower()
    mapping = {
        "advisory": "建议型：提供判断和建议，不拥有最终裁决权。",
        "建议型": "建议型：提供判断和建议，不拥有最终裁决权。",
        "collaborative": "协作型：参与协作方案、评审和推进，但不覆盖原 owner。",
        "协作型": "协作型：参与协作方案、评审和推进，但不覆盖原 owner。",
        "owner": "主导型：在其插入岗位/部门内拥有主导推进视角，仍受公司行为准则限制。",
        "主导型": "主导型：在其插入岗位/部门内拥有主导推进视角，仍受公司行为准则限制。",
        "decision": "决策型：可在模拟组织中提出决策方案，但不得覆盖公司行为准则、合规边界和真实治理结构。",
        "decision-support": "决策支持型：可提出决策备选方案和关键否决条件，最终裁决仍受原组织与公司准则约束。",
        "决策型": "决策型：可在模拟组织中提出决策方案，但不得覆盖公司行为准则、合规边界和真实治理结构。",
    }
    return mapping.get(value, mapping["advisory"])


def member_slot_role_description(member: dict[str, Any]) -> str:
    member_type = str(member.get("member_type") or "组织成员")
    level = str(member.get("level") or "")
    position = str(member.get("position") or "")
    combined = " ".join([member_type, level, position])
    if any(word in combined for word in ("老板", "董事", "创始", "CEO", "高管", "总裁")):
        return "老板/高层插槽成员：回答时可进入领导动作、战略取舍和资源重排视角，但不替换原老板 Skill。"
    if any(word in combined for word in ("负责人", "主管", "经理", "部长", "Owner", "owner")):
        return "部门负责人插槽成员：回答时重点体现 owner、资源协调、风险升级和交付责任。"
    if "顾问" in combined:
        return "顾问插槽成员：回答时提供专门建议和反事实视角，不拥有最终裁决权。"
    return "员工/组织成员插槽：回答时重点体现岗位执行、协作接口、风险反馈和落地动作。"


def should_pause_before_exit() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def pause_before_exit(prompt: str = "\n按回车键退出...") -> None:
    if should_pause_before_exit():
        try:
            input(prompt)
        except EOFError:
            pass


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件：{config_path}")
    return json.loads(read_text_with_encoding_fallback(path))


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"找不到 prompt 文件：{path}")
    return read_text_with_encoding_fallback(path)


def call_local_openai_compatible(prompt: str, model: str, api_base: str, api_key: str | None = None) -> str:
    base = (api_base or '').rstrip('/')
    if not base:
        raise RuntimeError('本地模型缺少 api_base。')
    url = f"{base}/chat/completions" if base.endswith('/v1') else f"{base}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if api_key and api_key != 'local-mode':
        headers['Authorization'] = f'Bearer {api_key}'
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_MODEL_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode('utf-8', errors='ignore'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'本地 OpenAI 兼容接口调用失败：HTTP {exc.code} - {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'无法连接本地 OpenAI 兼容接口：{exc}') from exc

    try:
        content = body['choices'][0]['message']['content']
    except Exception as exc:
        raise RuntimeError(f'本地接口返回格式异常：{body}') from exc
    return content if isinstance(content, str) else str(content)


def call_ollama_native(prompt: str, model: str, local_host: str | None = None) -> str:
    host = (local_host or 'http://localhost:11434').rstrip('/')
    if host.endswith('/v1'):
        host = host[:-3]
    url = f'{host}/api/generate'
    payload = {
        'model': model,
        'prompt': prompt,
        'stream': False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_MODEL_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode('utf-8', errors='ignore'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'Ollama 原生接口调用失败：HTTP {exc.code} - {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'无法连接 Ollama 原生接口：{exc}') from exc

    content = body.get('response')
    if not isinstance(content, str):
        raise RuntimeError(f'Ollama 返回格式异常：{body}')
    return content


def call_llm(
    prompt: str,
    model: str,
    api_key: str,
    api_base: str | None = None,
    *,
    model_type: str = 'cloud',
    local_model_type: str | None = None,
    local_host: str | None = None,
) -> str:
    prompt = with_output_rules(prompt)


    if model_type == 'local':
        logger.info('本地模型接口超时时间：%s 秒', LOCAL_MODEL_TIMEOUT_SECONDS)
        primary_error: Exception | None = None
        try:
            return sanitize_llm_output(call_local_openai_compatible(prompt, model, api_base or normalize_api_base('local', None, local_host), api_key))
        except Exception as exc:
            primary_error = exc
            logger.warning('本地 OpenAI 兼容接口调用失败，准备尝试回退方案：%s', exc)

        if (local_model_type or '').lower() == 'ollama':
            try:
                return sanitize_llm_output(call_ollama_native(prompt, model, local_host=local_host))
            except Exception as fallback_exc:
                raise RuntimeError(f'本地模型连接失败。先尝试 OpenAI 兼容接口失败：{primary_error}；再尝试 Ollama 原生接口也失败：{fallback_exc}') from fallback_exc

        raise RuntimeError(f'本地模型连接失败：{primary_error}') from primary_error

    if OpenAI is None:
        raise RuntimeError("未安装 openai SDK，请先运行 `pip install -r requirements.txt`。")

    client = OpenAI(api_key=api_key, base_url=api_base) if api_base else OpenAI(api_key=api_key)
    response = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
    content = response.choices[0].message.content
    return sanitize_llm_output(content if isinstance(content, str) else str(content))


def merge_request_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("version", "2.1")
    payload["company_slug"] = normalize_company_slug(payload.get("company_slug"), payload.get("company_name") or "company-skill")
    payload.setdefault("company_name", payload.get("company_slug", "company-skill"))
    payload.setdefault("model_type", "cloud")
    payload.setdefault("provider", "deepseek")
    payload.setdefault("company_files", [])
    payload.setdefault("organization", DEFAULT_ORGANIZATION.copy())
    payload.setdefault("personal_distillation", DEFAULT_PERSONAL.copy())
    payload.setdefault("confirmation_notes", "")

    organization = DEFAULT_ORGANIZATION.copy()
    organization.update(payload.get("organization", {}))
    detected_org_files = detect_org_chart_files(payload.get("company_files", []))
    if detected_org_files:
        organization["use_uploaded_org_chart"] = True
        if not organization.get("org_chart_files"):
            organization["org_chart_files"] = detected_org_files
    payload["organization"] = organization

    personal = DEFAULT_PERSONAL.copy()
    personal.update(payload.get("personal_distillation", {}))
    personal_file_paths = merge_file_path_values(personal.get("file_paths"), personal.get("file_path"))
    personal_model_file_paths = merge_file_path_values(personal.get("model_file_paths"), personal.get("model_file_path"))
    personal["file_paths"] = personal_file_paths
    personal["model_file_paths"] = personal_model_file_paths
    personal["file_path"] = personal_file_paths[0] if personal_file_paths else ""
    personal["model_file_path"] = personal_model_file_paths[0] if personal_model_file_paths else ""
    assignment = dict(personal.get("department_assignment") or {})
    if not assignment:
        assignment = {
            "mode": "manual" if personal.get("department") else "auto",
            "manual_department": personal.get("department", ""),
            "final_department": personal.get("department", ""),
        }
    personal["department_assignment"] = assignment
    payload["personal_distillation"] = personal

    raw_members = payload.get("organization_members") or []
    if not isinstance(raw_members, list):
        raw_members = []
    members: list[dict[str, Any]] = []
    for index, raw_member in enumerate(raw_members, start=1):
        if not isinstance(raw_member, dict):
            continue
        member = DEFAULT_ORGANIZATION_MEMBER.copy()
        member.update(raw_member)
        profile_file_paths = merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path"))
        skill_file_paths = merge_file_path_values(member.get("skill_file_paths"), member.get("model_file_paths"), member.get("skill_file_path"), member.get("model_file_path"))
        member["profile_file_paths"] = profile_file_paths
        member["skill_file_paths"] = skill_file_paths
        member["file_paths"] = profile_file_paths
        member["model_file_paths"] = skill_file_paths
        member["profile_file_path"] = profile_file_paths[0] if profile_file_paths else ""
        member["skill_file_path"] = skill_file_paths[0] if skill_file_paths else ""
        member["file_path"] = member.get("profile_file_path", "")
        member["model_file_path"] = member.get("skill_file_path", "")
        if not member.get("member_name"):
            member["member_name"] = member.get("position") or member.get("department") or f"组织成员{index}"
        member = enrich_organization_member_identity(member)
        member.setdefault("insert_mode", "append_non_destructive")
        member.setdefault("authority_level", "advisory")
        member.setdefault("department_assignment", {})
        members.append(member)
    payload["organization_members"] = members
    return payload


def build_request_from_legacy_args(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    company_name = args.user_query or args.company_slug
    request = {
        "version": "2.1",
        "company_slug": normalize_company_slug(args.company_slug, company_name),
        "company_name": company_name,
        "model_type": "local" if args.local else config.get("model_type", "cloud"),
        "provider": config.get("provider", "deepseek"),
        "company_files": [],
        "organization": DEFAULT_ORGANIZATION.copy(),
        "personal_distillation": DEFAULT_PERSONAL.copy(),
        "confirmation_notes": f"兼容旧版命令行输入：{args.user_query}",
    }
    return merge_request_defaults(request)


def build_runtime_settings(args: argparse.Namespace, config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    model_type = request.get("model_type") or ("local" if args.local else config.get("model_type", "cloud"))
    online_cfg = config.get("online", {})
    local_cfg = config.get("local", {})

    settings: dict[str, Any] = {
        "model_type": model_type,
        "provider": request.get("provider") or config.get("provider", "deepseek"),
        "model": args.model or request.get("model") or online_cfg.get("model") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        "api_key": args.api_key or request.get("api_key") or online_cfg.get("api_key") or os.getenv("OPENAI_API_KEY"),
        "api_base": args.api_base or request.get("api_base") or online_cfg.get("api_base") or os.getenv("OPENAI_API_BASE"),
        "local_model_type": request.get("local_model_type") or local_cfg.get("model_type") or "ollama",
        "local_model_name": args.local_model or request.get("local_model_name") or local_cfg.get("model_name") or "qwen2.5:7b",
        "local_host": args.local_host or request.get("local_host") or local_cfg.get("host") or "http://localhost:11434",
        "temperature": float(args.temperature or request.get("temperature") or local_cfg.get("temperature") or 0.7),
    }

    if model_type == "local":
        model_name = str(settings.get("local_model_name") or "").strip()
        if not model_name or model_name.lower() in {"auto", "自动", "自动识别", "auto-detect"}:
            detected = auto_select_local_model(settings.get("local_host"), settings.get("local_model_type"))
            if not detected:
                raise RuntimeError("未能自动识别本地模型。请确认 Ollama 已启动，或 LM Studio Local Server 已启动，然后重新运行。")
            settings["local_model_name"] = detected["model_name"]
            settings["local_model_type"] = detected.get("model_type") or settings.get("local_model_type")
            settings["local_host"] = detected.get("host") or settings.get("local_host")
            logger.info("自动识别到本地模型：%s (%s, %s)", detected.get("model_name"), detected.get("source"), detected.get("host"))
        settings["api_base"] = normalize_api_base("local", settings.get("api_base"), settings.get("local_host"))
        settings["api_key"] = settings.get("api_key") or "local-mode"
        settings["model"] = settings.get("local_model_name")
    return settings


def collect_enabled_organization_members(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all explicitly inserted organization members, plus legacy single-person config.

    Inserted members are non-destructive overlays on top of the auto-completed
    organization structure: they never replace the generated boss/employee defaults.
    """
    members: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for index, raw_member in enumerate(payload.get("organization_members") or [], start=1):
        if not isinstance(raw_member, dict) or not raw_member.get("enabled", True):
            continue
        member = DEFAULT_ORGANIZATION_MEMBER.copy()
        member.update(raw_member)
        profile_file_paths = merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path"))
        skill_file_paths = merge_file_path_values(member.get("skill_file_paths"), member.get("model_file_paths"), member.get("skill_file_path"), member.get("model_file_path"))
        member["profile_file_paths"] = profile_file_paths
        member["skill_file_paths"] = skill_file_paths
        member["file_paths"] = profile_file_paths
        member["model_file_paths"] = skill_file_paths
        member["profile_file_path"] = profile_file_paths[0] if profile_file_paths else ""
        member["skill_file_path"] = skill_file_paths[0] if skill_file_paths else ""
        member["file_path"] = member.get("profile_file_path", "")
        member["model_file_path"] = member.get("skill_file_path", "")
        if not member.get("member_name"):
            source_name = Path(member.get("profile_file_path") or member.get("skill_file_path") or "").stem
            member["member_name"] = member.get("position") or source_name or f"组织成员{index}"
        member = enrich_organization_member_identity(member)
        key = (
            str(member.get("member_name") or ""),
            str(member.get("profile_file_path") or ""),
            str(member.get("skill_file_path") or ""),
            str(member.get("department") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        members.append(member)

    personal = payload.get("personal_distillation", {}) or {}
    if personal.get("enabled") and (personal.get("file_path") or personal.get("model_file_path") or personal.get("position")):
        legacy = DEFAULT_ORGANIZATION_MEMBER.copy()
        legacy.update({
            "enabled": True,
            "member_name": personal.get("member_name") or personal.get("position") or Path(personal.get("file_path") or personal.get("model_file_path") or "组织成员").stem,
            "member_type": personal.get("member_type") or "组织成员",
            "level": personal.get("level", ""),
            "department": personal.get("department", ""),
            "position": personal.get("position", ""),
            "role_notes": personal.get("role_notes", ""),
            "has_peer_models": personal.get("has_peer_models", True),
            "profile_file_path": personal.get("file_path", ""),
            "profile_file_paths": personal.get("file_paths", []),
            "skill_file_path": personal.get("model_file_path", ""),
            "skill_file_paths": personal.get("model_file_paths", []),
            "file_path": personal.get("file_path", ""),
            "file_paths": personal.get("file_paths", []),
            "model_file_path": personal.get("model_file_path", ""),
            "model_file_paths": personal.get("model_file_paths", []),
            "department_assignment": personal.get("department_assignment", {}),
            "insert_mode": "append_non_destructive",
            "authority_level": personal.get("authority_level") or "advisory",
        })
        legacy = enrich_organization_member_identity(legacy)
        key = (
            str(legacy.get("member_name") or ""),
            str(legacy.get("profile_file_path") or ""),
            str(legacy.get("skill_file_path") or ""),
            str(legacy.get("department") or ""),
        )
        if key not in seen:
            members.append(legacy)

    return members


def ensure_organization_member_assignments(payload: dict[str, Any]) -> None:
    """Auto-fill departments for every inserted member that uses auto assignment."""
    org = payload.get("organization", {}) or {}
    candidates = list(org.get("selected_departments") or []) + list(org.get("custom_departments") or [])
    members = payload.get("organization_members") or []
    if not isinstance(members, list):
        return
    for member in members:
        if not isinstance(member, dict) or not member.get("enabled", True):
            continue
        profile_paths = merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path"))
        profile_path = profile_paths[0] if profile_paths else ""
        assignment = dict(member.get("department_assignment") or {})
        mode = str(assignment.get("mode") or "").lower()
        if mode == "manual" and member.get("department"):
            assignment.setdefault("manual_department", member.get("department", ""))
            assignment.setdefault("final_department", member.get("department", ""))
            member["department_assignment"] = assignment
            continue
        if not profile_path:
            assignment.setdefault("mode", "manual" if member.get("department") else "none")
            assignment.setdefault("final_department", member.get("department", ""))
            member["department_assignment"] = assignment
            continue
        try:
            result = classify_employee_files_department(profile_paths or profile_path, candidates=candidates)
        except Exception as exc:
            logger.warning("组织成员资料部门自动识别失败（%s）：%s", member.get("member_name") or profile_path, exc)
            assignment.update({"mode": "auto", "error": str(exc), "final_department": member.get("department", "")})
            member["department_assignment"] = assignment
            continue
        detected = result.get("detected_department") or result.get("department") or ""
        if detected and (not member.get("department") or mode == "auto"):
            member["department"] = detected
        result["final_department"] = member.get("department", "")
        result["manual_department"] = assignment.get("manual_department") or member.get("department", "")
        result["mode"] = "auto"
        member["department_assignment"] = result
        logger.info("组织成员[%s]部门自动识别：%s（置信度 %s%%）", member.get("member_name") or profile_path, detected or "未识别", result.get("confidence_percent"))


def render_organization_members_markdown(company_name: str, payload: dict[str, Any]) -> str:
    members = collect_enabled_organization_members(payload)
    lines = [
        f"# {company_name} · 组织插槽成员清单",
        "",
        "## 设计原则",
        "- 本文件记录用户非覆盖式插入到组织架构中的员工资料、员工 Skill 或外部来源 Skill 成员。",
        "- 插入成员是“组织插槽成员”：它们进入某个组织位置参与回答，但不会覆盖老板 Skill、员工集合体 Skill、公司行为准则或自动补全的组织结构。",
        "- 同一个组织位置可以插入多名成员；成员可以插入到任意层级，包括董事会/创始层、老板位、部门负责人或普通员工位。",
        "- 插入到老板位的成员应被视为“插槽老板/插槽领导成员”，不是普通外部顾问；但仍不得覆盖原老板 Skill 和 company-code.md。",
        "- 当插入成员建议与 company-code.md 冲突时，以 company-code.md 为准。",
        "",
        "## 权限级别说明",
        "- advisory / 建议型：提供判断和建议，不拥有最终裁决权。",
        "- collaborative / 协作型：参与协作方案、评审和推进，但不覆盖原 owner。",
        "- owner / 主导型：在插入岗位或部门内拥有主导推进视角，仍受公司行为准则限制。",
        "- decision / 决策型：在模拟组织中提出决策方案，但不得覆盖公司行为准则、合规边界和真实治理结构。",
        "",
    ]
    if not members:
        lines += ["## 当前插入成员", "暂无。"]
        return "\n".join(lines).strip()
    lines.append("## 当前插入成员")
    for idx, member in enumerate(members, start=1):
        assignment = member.get("department_assignment") or {}
        profile_paths = merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path"))
        skill_paths = merge_file_path_values(member.get("skill_file_paths"), member.get("model_file_paths"), member.get("skill_file_path"), member.get("model_file_path"))
        aliases = member.get("member_aliases") or []
        aliases_text = "、".join(aliases) if aliases else "无"
        lines += [
            "",
            f"### {idx}. {member.get('member_name') or member.get('position') or '未命名成员'}",
            f"- Skill 识别名：{member.get('skill_identity') or '未识别'}",
            f"- 可匹配别名：{aliases_text}",
            f"- 成员类型：{member.get('member_type') or '组织成员'}",
            f"- 插入层级：{member.get('level') or '未设置'}",
            f"- 插入部门：{member.get('department') or assignment.get('final_department') or '未设置'}",
            f"- 插入岗位/位置：{member.get('position') or '未设置'}",
            f"- 插槽身份判定：{member_slot_role_description(member)}",
            f"- 插入方式：{member.get('insert_mode') or 'append_non_destructive'}（非覆盖）",
            f"- 权限级别：{member.get('authority_level') or 'advisory'}",
            f"- 权限解释：{member_authority_description(member.get('authority_level'))}",
            f"- 员工资料文件：{format_file_names(profile_paths)}",
            f"- 员工 Skill / 模型文件：{format_file_names(skill_paths)}",
            f"- 部门归类方式：{assignment.get('mode') or '未设置'}",
            f"- 识别/标注依据：{assignment.get('reason') or '未填写'}",
            f"- 补充说明：{member.get('role_notes') or '无'}",
        ]
    lines += [
        "",
        "## 调用规则",
        "- 用户问题明确点名某个插入成员、Skill 识别名或别名时，必须先匹配本文件中的成员，再回答。",
        "- 如果成员插入到老板位、董事会/创始层、高管层或被标记为老板位成员，回答时应使用“组织插槽成员视角/插槽领导视角”，而不是只写成外部顾问建议。",
        "- 用户问题涉及该成员所在部门、岗位或职责时，可把成员作为该组织位置的补充视角。",
        "- 插入到老板位的成员只增加额外老板视角，不替换原老板 Skill。",
        "- 多名成员位于同一位置时，应并列比较其建议，并用 company-code.md 做最终边界校验。",
        "- 如果成员来源是外部 Skill，必须说明这是模拟组织成员，不代表真实任职关系或本人真实立场。",
    ]
    return "\n".join(lines).strip()


def ensure_personal_department_assignment(payload: dict[str, Any]) -> None:
    """Fill personal_distillation.department from local auto-classification when needed."""
    personal = payload.get("personal_distillation", {}) or {}
    personal_file_paths = merge_file_path_values(personal.get("file_paths"), personal.get("file_path"))
    if not personal.get("enabled") or not personal_file_paths:
        return
    assignment = dict(personal.get("department_assignment") or {})
    mode = str(assignment.get("mode") or "").lower()
    if mode == "manual" and personal.get("department"):
        assignment.setdefault("manual_department", personal.get("department", ""))
        assignment.setdefault("final_department", personal.get("department", ""))
        personal["department_assignment"] = assignment
        return
    org = payload.get("organization", {}) or {}
    candidates = list(org.get("selected_departments") or []) + list(org.get("custom_departments") or [])
    try:
        result = classify_employee_files_department(personal_file_paths, candidates=candidates)
    except Exception as exc:
        logger.warning("员工资料部门自动识别失败：%s", exc)
        assignment.update({"mode": "auto", "error": str(exc), "final_department": personal.get("department", "")})
        personal["department_assignment"] = assignment
        return
    detected = result.get("detected_department") or result.get("department") or ""
    if detected and (not personal.get("department") or mode == "auto"):
        personal["department"] = detected
    result["final_department"] = personal.get("department", "")
    result["manual_department"] = assignment.get("manual_department") or personal.get("department", "")
    result["mode"] = "auto"
    personal["department_assignment"] = result
    logger.info("员工资料部门自动识别：%s（置信度 %s%%）", detected or "未识别", result.get("confidence_percent"))


def prepare_materials(payload: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    ensure_personal_department_assignment(payload)
    ensure_organization_member_assignments(payload)
    personal = payload.get("personal_distillation", {})
    members = collect_enabled_organization_members(payload)
    company_files = [str(Path(path)) for path in payload.get("company_files", []) if path]

    for member in members:
        for path in merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path")):
            company_files.append(str(Path(path)))
        for path in merge_file_path_values(member.get("skill_file_paths"), member.get("model_file_paths"), member.get("skill_file_path"), member.get("model_file_path")):
            company_files.append(str(Path(path)))

    # 去重但保持顺序，避免同一文件在资料摘要中重复出现。
    company_files = list(dict.fromkeys(company_files))
    company_docs_summary = summarize_documents(company_files)

    search_results: list[dict[str, Any]] = []
    searchable_members = [m for m in members if not m.get("has_peer_models", True)]
    if searchable_members:
        target = searchable_members[0]
        search_results = search_latest_role_info(
            company_name=payload.get("company_name", ""),
            department=target.get("department", ""),
            position=target.get("position", ""),
        )
    elif personal.get("enabled") and not personal.get("has_peer_models", True):
        search_results = search_latest_role_info(
            company_name=payload.get("company_name", ""),
            department=personal.get("department", ""),
            position=personal.get("position", ""),
        )
    role_search_text = format_search_results(search_results)
    return company_docs_summary, role_search_text, search_results


def generate_preview_report(payload: dict[str, Any], settings: dict[str, Any]) -> tuple[Path, Path]:
    logger.info("开始生成预览确认报告")
    company_docs_summary, role_search_text, search_results = prepare_materials(payload)
    context = build_request_context(payload, company_docs_summary, role_search_text)
    preview_prompt = build_preview_prompt(context)
    preview_text = call_llm(
        preview_prompt,
        model=settings["model"],
        api_key=settings["api_key"],
        api_base=settings.get("api_base"),
        model_type=settings.get("model_type", "cloud"),
        local_model_type=settings.get("local_model_type"),
        local_host=settings.get("local_host"),
    )

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    preview_slug = payload.get("company_slug") or "preview"
    md_path = PREVIEW_DIR / f"{preview_slug}-preview-report.md"
    json_path = PREVIEW_DIR / f"{preview_slug}-preview-report.json"
    md_path.write_text(preview_text, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "request": redact_secrets(payload),
                "settings": redact_secrets(settings),
                "company_docs_summary": company_docs_summary,
                "role_search_text": role_search_text,
                "search_results": search_results,
                "preview_report": preview_text,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("预览报告生成完成：%s", md_path)
    print(f"PREVIEW_MD={md_path}")
    print(f"PREVIEW_JSON={json_path}")
    return md_path, json_path


def run_company_skill(payload: dict[str, Any], settings: dict[str, Any]) -> Path:
    payload = merge_request_defaults(payload)
    company_slug = payload["company_slug"]
    company_name = payload.get("company_name") or company_slug

    logger.info("开始运行公司技能生成流程 V2.1")
    logger.info("公司标识: %s", company_slug)
    if payload.get("clean_output"):
        removed_artifacts = clean_generated_artifacts(company_slug)
        if removed_artifacts:
            logger.info("已清理旧输出产物: %s", ", ".join(str(path) for path in removed_artifacts))
        else:
            logger.info("未发现需要清理的旧输出产物。")
    logger.info("公司名称: %s", company_name)
    logger.info("模型模式: %s", settings["model_type"])
    logger.info("使用模型: %s", settings["model"])

    company_docs_summary, role_search_text, _ = prepare_materials(payload)
    assert_materials_are_readable(company_docs_summary)
    context = build_request_context(payload, company_docs_summary, role_search_text)

    logger.info("加载 prompt 文件...")
    intake_template = load_prompt("intake.md")
    source_map_template = load_prompt("source_map.md")
    boss_builder_template = load_prompt("boss_builder.md")
    employee_builder_template = load_prompt("employee_collective_builder.md")
    code_builder_template = load_prompt("code_builder.md")
    router_builder_template = load_prompt("router_builder.md")
    runtime_answer_cases_template = load_prompt("runtime_answer_cases.md")
    runtime_output_contract_template = load_prompt("runtime_output_contract.md")
    runtime_compression_gate_template = load_prompt("runtime_compression_gate.md")
    source_confidence_protocol_template = load_prompt("source_confidence_protocol.md")
    source_trace_protocol_template = load_prompt("source_trace_protocol.md")
    prompt_governance_template = load_prompt("prompt_governance.md")
    cliche_rewrite_dictionary_template = load_prompt("cliche_rewrite_dictionary.md")
    model_prompt_profiles_template = load_prompt("model_prompt_profiles.md")
    prompt_release_lock_template = load_prompt("prompt_release_lock.md")
    strategy_builder_template = load_prompt("strategy_vision_builder.md")
    career_builder_template = load_prompt("career_path_builder.md")
    logger.info("所有 prompt 文件加载完成")
    common_prefix = build_cacheable_common_prefix(company_name, context)
    logger.info("已启用缓存友好 Prompt：公共资料固定前置，模块任务后置")

    logger.info("调用 intake 模块...")
    intake_prompt = build_module_prompt(
        common_prefix,
        "intake",
        intake_template,
        "### 本模块可使用的输入\n公共上下文已在本请求最前方给出，请基于公共上下文提取公司资料摘要。",
    )
    intake_result = call_llm(intake_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    logger.info("intake 模块调用完成")

    logger.info("调用 source_map 模块...")
    source_map_prompt = build_module_prompt(
        common_prefix,
        "source_map",
        source_map_template,
        f"""### intake 输出
{intake_result}""",
    )
    source_map_result = call_llm(source_map_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    logger.info("source_map 模块调用完成")

    logger.info("调用 boss_builder 模块...")
    boss_prompt = build_module_prompt(
        common_prefix,
        "boss_builder",
        boss_builder_template,
        f"""### 公司名
{company_name}

### 研究地图
{source_map_result}""",
    )
    boss_prompt = append_file_contract(boss_prompt, "boss-skill.md", company_name)
    boss_result = call_llm(boss_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    boss_result = ensure_output_contract("boss-skill.md", boss_result, company_name, context, settings)
    logger.info("boss_builder 模块调用完成")

    logger.info("调用 employee_collective_builder 模块...")
    employee_prompt = build_module_prompt(
        common_prefix,
        "employee_collective_builder",
        employee_builder_template,
        f"""### 公司名
{company_name}

### 研究地图
{source_map_result}""",
    )
    employee_prompt = append_file_contract(employee_prompt, "employee-collective-skill.md", company_name)
    employee_result = call_llm(employee_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    employee_result = ensure_output_contract("employee-collective-skill.md", employee_result, company_name, context, settings)
    logger.info("employee_collective_builder 模块调用完成")

    logger.info("调用 code_builder 模块...")
    code_prompt = build_module_prompt(
        common_prefix,
        "code_builder",
        code_builder_template,
        f"""### 公司名
{company_name}

### 研究地图
{source_map_result}""",
    )
    code_prompt = append_file_contract(code_prompt, "company-code.md", company_name)
    code_result = call_llm(code_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    code_result = ensure_output_contract("company-code.md", code_result, company_name, context, settings)
    logger.info("code_builder 模块调用完成")

    logger.info("调用 router_builder 模块...")
    router_prompt = build_module_prompt(
        common_prefix,
        "router_builder",
        router_builder_template,
        f"""### 公司名
{company_name}

### 老板 skill
{boss_result}

### 员工集合体 skill
{employee_result}

### 行为准则
{code_result}

### 运行时回答案例库
{runtime_answer_cases_template}

### 运行时输出合同
{runtime_output_contract_template}

### 运行时压缩门
{runtime_compression_gate_template}

### 证据强度与资料边界协议
{source_confidence_protocol_template}

### 资料短链与判断来源协议
{source_trace_protocol_template}

### Prompt 协议优先级与规则冲突裁决
{prompt_governance_template}

### 套话替换词典
{cliche_rewrite_dictionary_template}

### 模型提示适配协议
{model_prompt_profiles_template}

### Prompt 发布冻结协议
{prompt_release_lock_template}""",
    )
    router_prompt = append_file_contract(router_prompt, "SKILL.md", company_name)
    router_result = call_llm(router_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    router_result = ensure_output_contract("SKILL.md", router_result, company_name, context, settings)
    logger.info("router_builder 模块调用完成")

    logger.info("调用战略图景模块...")
    strategy_prompt = build_module_prompt(
        common_prefix,
        "strategy_vision_builder",
        strategy_builder_template,
        f"""### 公司名
{company_name}

### 三层输出
{boss_result}

{employee_result}

{code_result}""",
    )
    strategy_prompt = append_file_contract(strategy_prompt, "strategy-vision.md", company_name)
    strategy_result = call_llm(strategy_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
    strategy_result = ensure_output_contract("strategy-vision.md", strategy_result, company_name, context, settings)
    logger.info("战略图景模块调用完成")

    career_result = ""
    personal = payload.get("personal_distillation", {})
    members = collect_enabled_organization_members(payload)
    organization_members_md = render_organization_members_markdown(company_name, payload)
    if members:
        logger.info("已配置组织成员插入：%s 名", len(members))
    if personal.get("enabled") or members:
        logger.info("调用组织人员发展预测模块...")
        career_position = "多名组织成员" if members else (personal.get("position") or "目标岗位")
        career_prompt = build_module_prompt(
            common_prefix,
            "career_path_builder",
            career_builder_template,
            f"""### 公司名
{company_name}

### 组织成员插入清单
{organization_members_md}

### 组织插槽成员生成要求
- 这些成员是非覆盖式插入组织架构的“组织插槽成员”，不是覆盖原老板 Skill 或员工集合体 Skill 的替换项。
- 如果某成员插入到老板位、董事会/创始层、高管层，必须按插槽领导成员处理，说明其在模拟组织中的领导动作、主导范围、协作边界和受 company-code.md 约束的部分。
- 如果同一位置有多名成员，必须并列描述他们的差异、协同方式和冲突裁决方式。
- 如果成员来源是外部 Skill，必须说明这是模拟组织成员，不代表真实任职关系或本人真实立场。

### 三层输出
{boss_result}

{employee_result}

{code_result}""",
        )
        career_prompt = append_file_contract(career_prompt, "career-path.md", company_name, career_position)
        career_result = call_llm(career_prompt, model=settings["model"], api_key=settings["api_key"], api_base=settings.get("api_base"), model_type=settings.get("model_type", "cloud"), local_model_type=settings.get("local_model_type"), local_host=settings.get("local_host"))
        career_result = ensure_output_contract("career-path.md", career_result, company_name, context, settings, career_position)
        logger.info("组织人员发展预测模块调用完成")

    outputs_for_validation = {
        "boss-skill.md": boss_result,
        "employee-collective-skill.md": employee_result,
        "company-code.md": code_result,
        "SKILL.md": router_result,
        "strategy-vision.md": strategy_result,
    }
    if career_result:
        outputs_for_validation["career-path.md"] = career_result
    # Always emit organization-members.md so GUI/API quality checks have a stable
    # file contract. When no members are inserted, the renderer returns a clear
    # "暂无" roster rather than omitting the file.
    if organization_members_md:
        outputs_for_validation["organization-members.md"] = organization_members_md

    outputs_for_validation = {name: normalize_final_output(name, text) for name, text in outputs_for_validation.items()}
    validate_generated_outputs(outputs_for_validation)
    boss_result = outputs_for_validation["boss-skill.md"]
    employee_result = outputs_for_validation["employee-collective-skill.md"]
    code_result = outputs_for_validation["company-code.md"]
    router_result = outputs_for_validation["SKILL.md"]
    strategy_result = outputs_for_validation["strategy-vision.md"]
    career_result = outputs_for_validation.get("career-path.md", "")
    organization_members_md = outputs_for_validation.get("organization-members.md", organization_members_md)

    out_dir = OUTPUT_BASE / company_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("创建输出目录: %s", out_dir)

    logger.info("写入输出文件...")
    (out_dir / "boss-skill.md").write_text(boss_result, encoding="utf-8")
    (out_dir / "employee-collective-skill.md").write_text(employee_result, encoding="utf-8")
    (out_dir / "company-code.md").write_text(code_result, encoding="utf-8")
    (out_dir / "SKILL.md").write_text(router_result, encoding="utf-8")
    (out_dir / "strategy-vision.md").write_text(strategy_result, encoding="utf-8")
    if career_result:
        (out_dir / "career-path.md").write_text(career_result, encoding="utf-8")
    if organization_members_md:
        (out_dir / "organization-members.md").write_text(organization_members_md, encoding="utf-8")

    (out_dir / "generation-request.json").write_text(json.dumps(redact_secrets(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "generation-summary.md").write_text(
        "\n".join(
            [
                f"# {company_name} 生成摘要",
                "",
                f"- 版本：{payload.get('version', '2.1')}",
                f"- 模型模式：{settings['model_type']}",
                f"- 输出目录：{out_dir}",
                f"- 已启用组织人员蒸馏：{'是' if personal.get('enabled') or members else '否'}",
                f"- 插入组织成员数：{len(members)}",
                f"- 组织部门数：{len(payload.get('organization', {}).get('selected_departments', []))}",
                f"- 部门归类方式：{personal.get('department_assignment', {}).get('mode', '未启用') if personal.get('enabled') else '未启用'}",
                f"- 自动识别部门：{personal.get('department_assignment', {}).get('detected_department', '') if personal.get('enabled') else ''}",
                f"- 最终所属部门：{personal.get('department_assignment', {}).get('final_department', personal.get('department', '')) if personal.get('enabled') else ''}",
                "",
                "## 联网岗位信息摘要",
                role_search_text,
                "",
                "## 资料摘要",
                company_docs_summary or "暂无",
            ]
        ),
        encoding="utf-8",
    )
    logger.info("所有文件写入完成")

    logger.info("生成 Codex / Claude Code Agent 兼容输出...")
    agent_export = export_agent_packages(out_dir, AGENT_EXPORT_BASE, company_name, company_slug)
    logger.info("Agent Skill 名称: %s", agent_export["skill_name"])
    logger.info("Codex 兼容输出目录: %s", agent_export["codex_dir"])
    logger.info("Claude Code 兼容输出目录: %s", agent_export["claude_code_dir"])

    logger.info("公司技能生成流程完成")
    for name in ["boss-skill.md", "employee-collective-skill.md", "company-code.md", "SKILL.md", "strategy-vision.md"]:
        logger.info("  - %s", out_dir / name)
    if career_result:
        logger.info("  - %s", out_dir / "career-path.md")
    if organization_members_md:
        logger.info("  - %s", out_dir / "organization-members.md")
    print(f"OUTPUT_DIR={out_dir}")
    print(f"CODEX_AGENT_DIR={agent_export['codex_dir']}")
    print(f"CLAUDE_CODE_AGENT_DIR={agent_export['claude_code_dir']}")
    return out_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行 算疏智合 v0.1.0 生成流程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python run.py huawei "华为" --api-key sk-xxxxx
  python run.py --request-file temp_request.json --preview-only --api-key sk-xxxxx
  python run.py --request-file temp_request.json --local --local-model qwen2.5:7b --local-host http://localhost:11434
        """,
    )
    parser.add_argument("company_slug", nargs="?", help="兼容旧版：输出目录名称，例如 my-company")
    parser.add_argument("user_query", nargs="?", help="兼容旧版：公司名称或旧版用户查询")
    parser.add_argument("--request-file", help="V2.1 请求 JSON 文件路径")
    parser.add_argument("--preview-only", action="store_true", help="只生成确认预览报告，不正式产出技能文件")
    parser.add_argument("--clean-output", action="store_true", help="正式生成前清理同名 companies/agent_exports/preview_reports 旧产物")
    parser.add_argument("--config", help="JSON 配置文件路径，默认使用 config.json")
    parser.add_argument("--model", help="LLM 模型名称")
    parser.add_argument("--api-key", help="OpenAI 兼容 API Key")
    parser.add_argument("--api-base", help="OpenAI 兼容接口地址，例如 Deepseek API 地址")
    parser.add_argument("--local", action="store_true", help="启用本地模型模式")
    parser.add_argument("--local-model", help="本地模型名称，例如 qwen2.5:7b；也可传 auto/自动识别 让程序自动扫描本地模型")
    parser.add_argument("--local-host", help="本地 OpenAI 兼容服务地址，例如 http://localhost:11434")
    parser.add_argument("--temperature", help="本地模型温度参数，保留兼容")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        config = {}
        config_path = args.config or "config.json"
        if Path(config_path).exists():
            config = load_config(config_path)

        if args.request_file:
            request = merge_request_defaults(load_config(args.request_file))
        elif args.company_slug:
            request = build_request_from_legacy_args(args, config)
        else:
            print("\n❌ 错误：缺少必需参数！")
            print("   你可以使用旧版命令：python run.py <公司标识> \"<公司名>\"")
            print("   或使用新版命令：python run.py --request-file temp_request.json")
            pause_before_exit()
            sys.exit(1)

        if getattr(args, "clean_output", False):
            request["clean_output"] = True

        settings = build_runtime_settings(args, config, request)
        if not settings.get("api_key"):
            print("\n❌ 错误：API Key 未配置！")
            print("   云端模式需要在 config.json、request 文件或命令行中提供 api_key。")
            print("   本地模式请加 --local，程序会自动兼容本地 OpenAI 风格接口。")
            pause_before_exit()
            sys.exit(1)

        if args.preview_only:
            generate_preview_report(request, settings)
        else:
            run_company_skill(request, settings)

    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("程序执行失败")
        print(f"\n❌ 程序执行失败：{exc}")
        pause_before_exit()
        sys.exit(1)

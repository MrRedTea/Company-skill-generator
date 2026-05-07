"""Agent-compatible export helpers for generated company Skill packages.

This module converts the normal companies/<slug>/ Markdown output into
filesystem layouts that Codex and Claude Code can discover directly:

- agent_exports/<slug>/codex/.agents/skills/<skill-name>/SKILL.md
- agent_exports/<slug>/claude-code/.claude/skills/<skill-name>/SKILL.md

The original generated files remain untouched under companies/<slug>/.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

AGENT_EXPORTS_DIRNAME = "agent_exports"
REFERENCE_FILE_MAP = {
    "SKILL.md": "org-router.md",
    "boss-skill.md": "boss-skill.md",
    "employee-collective-skill.md": "employee-collective-skill.md",
    "company-code.md": "company-code.md",
    "strategy-vision.md": "strategy-vision.md",
    "career-path.md": "career-path.md",
    "organization-members.md": "organization-members.md",
    "generation-summary.md": "generation-summary.md",
}


def _ascii_skill_name(company_name: str, company_slug: str) -> str:
    """Return a conservative skill directory/name usable across agents.

    Keep ASCII lowercase/hyphen names because they work well for explicit
    invocations such as `$huawei-org-skill` and `/huawei-org-skill`.
    """
    source = " ".join([company_slug or "", company_name or ""]).lower()
    if "huawei" in source or "华为" in source:
        return "huawei-org-skill"

    ascii_source = re.sub(r"[^a-z0-9]+", "-", (company_slug or company_name or "company").lower()).strip("-")
    ascii_source = re.sub(r"-+", "-", ascii_source)
    if not ascii_source:
        ascii_source = "company"
    if not ascii_source.endswith("org-skill"):
        ascii_source = f"{ascii_source}-org-skill"
    return ascii_source[:80].strip("-") or "company-org-skill"


def _display_name(company_name: str, company_slug: str) -> str:
    return (company_name or company_slug or "公司").strip()


def _copy_references(source_dir: Path, references_dir: Path) -> list[str]:
    references_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source_name, target_name in REFERENCE_FILE_MAP.items():
        src = source_dir / source_name
        if not src.exists() or not src.is_file():
            continue
        dst = references_dir / target_name
        dst.write_bytes(src.read_bytes())
        copied.append(target_name)
    return copied


def _reference_bullets(copied: Iterable[str]) -> str:
    labels = {
        "org-router.md": "原项目输出的总入口路由规则。",
        "boss-skill.md": "老板 / 高层决策视角。",
        "employee-collective-skill.md": "员工集合体与部门协作视角。",
        "company-code.md": "公司行为准则、风险边界、冲突裁决规则。",
        "strategy-vision.md": "战略愿景，包含事实、推断、预测分层。",
        "career-path.md": "组织人员蒸馏结果，包含被插入成员的发展路径、能力画像或角色推演。",
        "organization-members.md": "组织插槽成员清单，说明员工资料/员工 Skill 被非覆盖式插入到哪些组织位置，以及其权限边界。",
        "generation-summary.md": "公司资料摘要，仅作为事实背景参考。",
    }
    return "\n".join(f"- `references/{name}`：{labels.get(name, '参考资料。')}" for name in copied)


def _skill_body(display_name: str, copied: list[str], platform_name: str) -> str:
    refs = _reference_bullets(copied) or "- 当前没有可用 references 文件。"
    invocation_hint = "$<skill-name>" if platform_name == "Codex" else "/<skill-name>"
    return f"""# {display_name} 组织 Skill for {platform_name}

你正在使用一套由算疏智合输出的组织 Skill。它不是普通背景资料，而是一套“原组织自动补全 + 可插拔组织成员”的多角色组织系统。

## 参考文件

按任务需要读取 `references/` 下的文件：

{refs}

## 核心概念：组织插槽成员

`references/organization-members.md` 中的成员不是简单的外部顾问，也不是替换原老板 Skill 或员工集合体 Skill 的覆盖项。它们是非覆盖式插入到组织架构某个位置的“组织插槽成员”。

使用时必须先判断：

1. 用户是否点名某个插入成员，例如 Elon Musk、马斯克、某个员工或某个 Skill 成员。
2. 该成员在 `organization-members.md` 中的插入层级、插入部门、插入岗位/位置、成员类型和权限级别。
3. 该成员是老板位成员、部门负责人、普通员工、顾问，还是外部来源的 Skill 成员。
4. 该成员与原始自动生成的老板 Skill、员工集合体 Skill、公司行为准则之间是“并列叠加关系”，不是替换关系。

如果用户问“某成员如何带领公司”“某成员如果坐在老板位会怎么做”，并且该成员被插入到老板位、董事会/创始层、高管层或被标注为老板位成员，必须把他作为组织结构中的“插槽老板/插槽领导成员”回答，而不是只写成外部顾问建议。

## 插槽权限解释

按 `organization-members.md` 中的权限级别处理：

- `advisory` / 建议型：该成员提供建议，不拥有最终裁决权。
- `collaborative` / 协作型：该成员可参与协作方案、评审和推进，但不能覆盖原 owner。
- `owner` / 主导型：该成员在其插入岗位/部门内拥有主导推进视角，但仍受 `company-code.md` 限制。
- `decision` / 决策型：该成员可在模拟组织中提出决策方案，但不得覆盖公司行为准则、合规边界和真实治理结构。
- `decision-support` / 决策支持型：该成员可提出决策备选方案、关键否决条件和风险校验，最终裁决仍受原组织与公司准则约束。

如果字段缺失，默认按 `advisory` 处理。

## 使用规则

战略判断、资源取舍、组织方向问题，优先使用 `references/boss-skill.md`。

执行推进、需求评审、跨部门协作、上线复盘问题，优先使用 `references/employee-collective-skill.md`。

规则、风险、权限、合规、组织冲突问题，必须使用 `references/company-code.md`。

涉及未来路线、战略阶段、业务节奏时，使用 `references/strategy-vision.md`，并区分 `[资料事实]`、`[合理推断]`、`[高不确定预测]`。

涉及被导入的员工资料、员工 Skill、外部来源 Skill 或多名插入成员时，必须先读取 `references/organization-members.md` 判断插入位置，再按其插槽身份调用 `references/career-path.md`、成员 Skill 内容和对应组织层级文件。

例如 Elon Musk 作为被插入的老板位成员、战略成员或外部来源 Skill 成员时，必须说明：

- 这是“组织插槽成员视角”，不是普通外部顾问视角；
- 如果其来源是外部 Skill，则说明不代表公司真实任职关系，也不代表本人或公司真实立场；
- 该成员是非覆盖式叠加，不替换原老板 Skill、员工集合体 Skill 或自动补全组织结构；
- 该成员建议不能覆盖 `references/company-code.md`；
- 如果插槽成员建议与公司行为准则冲突，以 `references/company-code.md` 为准。

## 多成员处理规则

如果同一组织位置插入多名成员，不要只选择一个成员。应并列列出：

- 每名成员的插入位置；
- 各自会强调的关键判断；
- 可能产生的冲突；
- 最终由 `company-code.md`、老板 Skill 和组织目标进行边界校验。

## 回答输出模式

默认使用结论版 / 决策卡回答，普通问题只给结论、必要依据和最小动作；只有用户明确要求完整、详细、报告或分层拆解时，才输出完整版。

当用户明确要求“分析一下”“讲清楚原因”“怎么推进”时，使用分析版；当用户明确要求“完整版”“完整报告”“分层拆解”时，才使用报告版。当用户要求“简易版”“简短回答”“发言人形式”“对外口径”“官方口径”“给外部看的回答”“只要结论”时，使用“简易版 / 发言人结论模式”，只输出最终口径。

简易版规则：

- 只输出 `[发言人简易版]`，除非用户明确要求“完整版 + 简易版”。
- 不逐段暴露老板视角、员工集合体视角、组织插槽成员视角和行为准则校验的完整拆解。
- 不输出内部推理过程，不写“我先分析”。
- 大幅简略：通常 1 段，最多 2 段；只给结论，不做长展开。
- 必须保留被点名员工 Skill / 组织插槽成员的语气，例如马斯克式第一性原理、极限工程、速度与成本压力、减少复杂度。
- 如涉及外部/插槽成员，最多用一句短边界说明；不要展开长免责声明。
- 若用户问的是代码修改/项目实现，简易版仍要给出可执行结论，但不展开完整技术推理。

## {platform_name} 执行方式

当用户通过 `{invocation_hint}` 显式调用，或任务明显涉及本组织 Skill 时，先判断用户需要结论版、分析版、报告版还是简易版，再做问题归类。

仅当用户明确要求完整、详细、报告或分层拆解时，才使用以下报告版结构：

[问题归类]

[组织插槽成员识别]

[老板视角]

[员工集合体视角]

[组织插槽成员视角，如适用]

[多成员协同/冲突，如适用]

[行为准则校验]

[组织落地建议]

[验证方式]

[诚实边界]

简易版 / 发言人结论模式只输出：

[发言人简易版]

只有当用户的问题明确要求审查代码、修改仓库、生成补丁、实现功能、修复 bug 或解释项目代码时，才输出：

[代码/项目修改建议]

不要在纯战略、组织、管理、角色模拟问题中强行输出“本次不需要修改代码”。
"""

def _codex_skill_md(skill_name: str, display_name: str, copied: list[str]) -> str:
    desc = (
        f"当需要 Codex 按当前项目生成的{display_name}组织系统、老板视角、员工集合体、"
        "公司行为准则、战略愿景、组织人员蒸馏或组织插槽成员视角进行分析、审查、设计或代码修改时使用。"
    )
    return f"""---
name: {skill_name}
description: {desc}
---

{_skill_body(display_name, copied, 'Codex')}"""


def _claude_skill_md(skill_name: str, display_name: str, copied: list[str]) -> str:
    desc = (
        f"当需要 Claude Code 按当前项目生成的{display_name}组织系统、老板视角、员工集合体、"
        "公司行为准则、战略愿景、组织人员蒸馏或组织插槽成员视角进行分析、审查、设计或代码修改时使用。"
    )
    return f"""---
name: {skill_name}
description: {desc}
---

{_skill_body(display_name, copied, 'Claude Code')}"""


def _codex_agents_md(skill_name: str, display_name: str) -> str:
    return f"""# AGENTS.md

本仓库包含一个由算疏智合输出的组织 Skill：

- `.agents/skills/{skill_name}/`

当任务涉及{display_name}组织模拟、公司治理、老板视角、员工集合体、公司行为准则、战略愿景、组织人员蒸馏或组织插槽成员时，请优先使用 `${skill_name}`。

不要把 `generation-request.json` 当作知识文件使用。

被插入的员工资料、员工 Skill 或外部来源 Skill 成员一律视为“组织插槽成员”。它们是非覆盖式叠加，不替换原老板 Skill 或员工集合体；其建议不得覆盖 `company-code.md`。

默认使用结论版 / 决策卡；只有用户明确要求完整、详细、报告或分层拆解时，才输出完整版。用户要求简易版、发言人形式、对外口径或只要结论时，只输出极简 `[发言人简易版]`。
"""


def _claude_md(skill_name: str, display_name: str) -> str:
    return f"""# CLAUDE.md

本仓库包含一个由算疏智合输出的 Claude Code Skill：

- `.claude/skills/{skill_name}/`

当任务涉及{display_name}组织系统、老板视角、员工集合体、公司行为准则、战略愿景、组织人员蒸馏或组织插槽成员时，请使用 `/{skill_name}`。

不要把 `generation-request.json` 当作知识文件使用。

被插入的员工资料、员工 Skill 或外部来源 Skill 成员一律视为“组织插槽成员”。它们是非覆盖式叠加，不替换原老板 Skill 或员工集合体；其建议不得覆盖 `company-code.md`。

默认使用结论版 / 决策卡；只有用户明确要求完整、详细、报告或分层拆解时，才输出完整版。用户要求简易版、发言人形式、对外口径或只要结论时，只输出极简 `[发言人简易版]`。
"""


def _readme(display_name: str, skill_name: str) -> str:
    return f"""# Agent 兼容输出包

这是由算疏智合自动导出的 Agent 兼容目录。

## Codex 使用方式

把 `codex/` 目录下的内容复制到 Codex 工作仓库根目录。复制后应出现：

```text
.agents/skills/{skill_name}/SKILL.md
AGENTS.md
```

在 Codex 中可使用：

```text
${skill_name} 请按{display_name}组织 Skill 分析当前任务。
```

简易版调用示例：

```text
${skill_name} 请用发言人简易版只输出结论：马斯克如何带领组织走向下一步？
```

## Claude Code 使用方式

把 `claude-code/` 目录下的内容复制到 Claude Code 工作仓库根目录。复制后应出现：

```text
.claude/skills/{skill_name}/SKILL.md
CLAUDE.md
```

在 Claude Code 中可使用：

```text
/{skill_name} 请按{display_name}组织 Skill 分析当前任务。
```

简易版调用示例：

```text
/{skill_name} 请用发言人简易版只输出结论：马斯克如何带领组织走向下一步？
```

## 注意

- `generation-request.json` 不会复制到 Agent Skill 中。
- 原项目输出的 `SKILL.md` 已保存为 `references/org-router.md`。
- 被插入的员工资料、员工 Skill 或外部来源 Skill 成员是“组织插槽成员”，不是覆盖原组织结构的替换项。
"""


def export_agent_packages(source_dir: Path, export_root: Path, company_name: str, company_slug: str) -> dict[str, str]:
    """Create Codex and Claude Code compatible exports for a generated company package.

    Args:
        source_dir: companies/<slug> directory containing generated Markdown files.
        export_root: project-level agent_exports directory.
        company_name: human readable company name.
        company_slug: output slug used for directory naming.

    Returns:
        Mapping with codex_dir, claude_code_dir, skill_name and readme paths.
    """
    source_dir = Path(source_dir)
    export_root = Path(export_root)
    display = _display_name(company_name, company_slug)
    skill_name = _ascii_skill_name(company_name, company_slug)
    package_root = export_root / (company_slug or skill_name)

    codex_skill_dir = package_root / "codex" / ".agents" / "skills" / skill_name
    codex_refs_dir = codex_skill_dir / "references"
    codex_copied = _copy_references(source_dir, codex_refs_dir)
    (codex_skill_dir / "SKILL.md").write_text(_codex_skill_md(skill_name, display, codex_copied), encoding="utf-8")
    (package_root / "codex" / "AGENTS.md").write_text(_codex_agents_md(skill_name, display), encoding="utf-8")

    claude_skill_dir = package_root / "claude-code" / ".claude" / "skills" / skill_name
    claude_refs_dir = claude_skill_dir / "references"
    claude_copied = _copy_references(source_dir, claude_refs_dir)
    (claude_skill_dir / "SKILL.md").write_text(_claude_skill_md(skill_name, display, claude_copied), encoding="utf-8")
    (package_root / "claude-code" / "CLAUDE.md").write_text(_claude_md(skill_name, display), encoding="utf-8")

    readme = package_root / "README_AGENT_EXPORT.md"
    readme.write_text(_readme(display, skill_name), encoding="utf-8")

    return {
        "skill_name": skill_name,
        "package_root": str(package_root),
        "codex_dir": str(package_root / "codex"),
        "claude_code_dir": str(package_root / "claude-code"),
        "readme": str(readme),
    }

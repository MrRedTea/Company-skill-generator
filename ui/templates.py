"""Project template library for 算疏智合.

Keeps the built-in templates and adds a small editable custom
library stored as JSON on disk. Custom templates are intentionally separated from
built-ins so upgrades can refresh built-ins without overwriting user work.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


CUSTOM_TEMPLATE_DIR = Path("template_library")
CUSTOM_TEMPLATE_PATH = CUSTOM_TEMPLATE_DIR / "custom_templates.json"


@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    name: str
    description: str
    selected_departments: list[str]
    custom_departments: list[str]
    org_notes: str
    confirmation_notes: str
    recommended_members: list[dict[str, Any]]
    source: str = "builtin"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BUILTIN_TEMPLATES: list[ProjectTemplate] = [
    ProjectTemplate(
        id="tech_ai_data",
        name="科技 / AI / 数据公司",
        description="适合软件、AI 应用、数据平台、SaaS、互联网产品团队。强调研发、产品、算法、基础设施与商业化闭环。",
        selected_departments=[
            "董事会/创始办公室",
            "战略与投资部",
            "研发工程部",
            "产品管理部",
            "设计与用户体验部",
            "数据与算法部",
            "信息技术/基础设施部",
            "市场品牌部",
            "销售商务部",
            "客户成功/客服部",
            "运营管理部",
            "人力资源部",
            "财务部",
            "法务与合规部",
        ],
        custom_departments=["AI 平台部", "数据治理部", "安全与可信 AI 小组", "开发者生态部"],
        org_notes="以产品、研发、算法、数据和商业化为核心，强调从技术能力到可交付产品、客户成功和生态扩展的闭环。",
        confirmation_notes="生成时突出技术路线、产品化能力、数据治理、模型安全、平台生态和商业落地节奏。",
        recommended_members=[
            {"name": "创始人 / CEO", "level": "CEO/总裁层", "department": "董事会/创始办公室", "position": "公司方向与资源配置"},
            {"name": "CTO", "level": "CXO/高管层", "department": "研发工程部", "position": "技术路线与平台架构"},
            {"name": "产品负责人", "level": "一级部门负责人", "department": "产品管理部", "position": "产品战略与需求优先级"},
        ],
    ),
    ProjectTemplate(
        id="manufacturing_industry",
        name="制造业 / 工业企业",
        description="适合装备制造、电子制造、汽车零部件、工业自动化企业。强调供应链、质量、生产、研发和交付。",
        selected_departments=[
            "董事会/创始办公室",
            "战略与投资部",
            "研发工程部",
            "产品管理部",
            "供应链/采购部",
            "运营管理部",
            "销售商务部",
            "客户成功/客服部",
            "人力资源部",
            "财务部",
            "法务与合规部",
            "行政与公共事务部",
        ],
        custom_departments=["生产制造部", "质量管理部", "设备工程部", "工艺工程部", "仓储物流部"],
        org_notes="以研发、工艺、生产、质量、供应链和交付为主线，关注成本、良率、交期、库存和客户验收。",
        confirmation_notes="生成时突出工程制造能力、质量体系、供应链韧性、降本增效、安全生产和客户交付。",
        recommended_members=[
            {"name": "总经理", "level": "CEO/总裁层", "department": "董事会/创始办公室", "position": "经营目标与产能决策"},
            {"name": "生产负责人", "level": "一级部门负责人", "department": "生产制造部", "position": "产能、排产与现场管理"},
            {"name": "质量负责人", "level": "一级部门负责人", "department": "质量管理部", "position": "质量体系与问题闭环"},
        ],
    ),
    ProjectTemplate(
        id="consulting_service",
        name="咨询 / 专业服务公司",
        description="适合管理咨询、数据咨询、法律财税、人力资源服务、交付型项目团队。强调客户洞察、方法论、项目交付和复购。",
        selected_departments=[
            "董事会/创始办公室",
            "战略与投资部",
            "市场品牌部",
            "销售商务部",
            "客户成功/客服部",
            "运营管理部",
            "人力资源部",
            "财务部",
            "法务与合规部",
            "数据与算法部",
        ],
        custom_departments=["咨询交付部", "行业研究部", "知识管理部", "项目管理办公室 PMO"],
        org_notes="以客户获取、方案设计、项目交付、知识沉淀和客户复购为主线，强调方法论和交付质量。",
        confirmation_notes="生成时突出行业洞察、顾问方法论、项目管理、客户沟通、知识库沉淀和复购路径。",
        recommended_members=[
            {"name": "合伙人 / 负责人", "level": "CEO/总裁层", "department": "董事会/创始办公室", "position": "客户战略与业务拓展"},
            {"name": "项目总监", "level": "一级部门负责人", "department": "咨询交付部", "position": "项目交付与质量控制"},
            {"name": "行业研究负责人", "level": "一级部门负责人", "department": "行业研究部", "position": "研究框架与知识沉淀"},
        ],
    ),
    ProjectTemplate(
        id="school_research",
        name="学校 / 实验室 / 研究机构",
        description="适合高校学院、实验室、科研团队、研究中心。强调课题方向、科研产出、人才培养和项目合作。",
        selected_departments=[
            "董事会/创始办公室",
            "战略与投资部",
            "人力资源部",
            "财务部",
            "法务与合规部",
            "数据与算法部",
            "信息技术/基础设施部",
            "行政与公共事务部",
        ],
        custom_departments=["科研项目部", "学术委员会", "研究生培养办公室", "实验平台与仪器中心", "成果转化办公室"],
        org_notes="以学科方向、科研课题、实验平台、人才培养、成果发表和产学研合作为主线。",
        confirmation_notes="生成时突出研究方向规划、课题组织、论文/专利产出、学生培养、实验平台管理和合作资源。",
        recommended_members=[
            {"name": "课题组负责人 / PI", "level": "CEO/总裁层", "department": "科研项目部", "position": "研究方向与课题资源"},
            {"name": "实验平台负责人", "level": "一级部门负责人", "department": "实验平台与仪器中心", "position": "实验资源与技术支撑"},
            {"name": "研究骨干", "level": "核心骨干", "department": "科研项目部", "position": "具体课题推进"},
        ],
    ),
    ProjectTemplate(
        id="personal_ip_team",
        name="个人 IP / 内容团队",
        description="适合自媒体、UP 主、知识博主、直播电商、内容工作室。强调选题、内容生产、粉丝运营和商业化。",
        selected_departments=[
            "董事会/创始办公室",
            "产品管理部",
            "设计与用户体验部",
            "数据与算法部",
            "市场品牌部",
            "销售商务部",
            "客户成功/客服部",
            "运营管理部",
            "财务部",
            "法务与合规部",
        ],
        custom_departments=["内容策划部", "拍摄剪辑组", "账号运营组", "商务合作组", "直播运营组"],
        org_notes="以个人 IP 定位、内容选题、制作发布、粉丝增长、商务合作和变现路径为主线。",
        confirmation_notes="生成时突出人设定位、内容栏目、粉丝画像、平台策略、商业合作、直播/课程/社群等变现方式。",
        recommended_members=[
            {"name": "IP 主理人", "level": "CEO/总裁层", "department": "董事会/创始办公室", "position": "内容方向与品牌人格"},
            {"name": "内容策划", "level": "团队负责人", "department": "内容策划部", "position": "选题策划与栏目设计"},
            {"name": "运营负责人", "level": "团队负责人", "department": "账号运营组", "position": "账号增长与数据复盘"},
        ],
    ),
    ProjectTemplate(
        id="public_institution",
        name="政府 / 事业单位 / 公共组织",
        description="适合政务部门、事业单位、公共服务机构。强调制度合规、服务流程、公共沟通和风险边界。",
        selected_departments=[
            "董事会/创始办公室",
            "战略与投资部",
            "人力资源部",
            "财务部",
            "法务与合规部",
            "信息技术/基础设施部",
            "客户成功/客服部",
            "运营管理部",
            "行政与公共事务部",
        ],
        custom_departments=["政务服务部", "政策研究室", "公共沟通办公室", "监督审计办公室", "数据资源管理部"],
        org_notes="以政策执行、公共服务、流程规范、风险防控、数据治理和公众沟通为主线。",
        confirmation_notes="生成时突出依法合规、公共服务质量、跨部门协同、信息公开、风险边界和数据安全。",
        recommended_members=[
            {"name": "主要负责人", "level": "CEO/总裁层", "department": "董事会/创始办公室", "position": "组织治理与公共责任"},
            {"name": "政务服务负责人", "level": "一级部门负责人", "department": "政务服务部", "position": "服务流程与群众反馈"},
            {"name": "合规监督负责人", "level": "一级部门负责人", "department": "监督审计办公室", "position": "制度监督与风险控制"},
        ],
    ),
]


def _slugify(value: str) -> str:
    text = str(value or "template").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    return text or "template"


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\n,，、;；]+", value) if part.strip()]
    return []


def _members_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        members: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    members.append({
                        "name": name,
                        "level": str(item.get("level") or "").strip(),
                        "department": str(item.get("department") or "").strip(),
                        "position": str(item.get("position") or "").strip(),
                    })
        return members
    if isinstance(value, str):
        members = []
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in re.split(r"[|｜]", line)]
            while len(parts) < 4:
                parts.append("")
            members.append({"name": parts[0], "level": parts[1], "department": parts[2], "position": parts[3]})
        return members
    return []


def template_from_dict(data: dict[str, Any], source: str = "custom") -> ProjectTemplate:
    name = str(data.get("name") or "未命名模板").strip()
    template_id = str(data.get("id") or _slugify(name)).strip()
    return ProjectTemplate(
        id=template_id,
        name=name,
        description=str(data.get("description") or "").strip(),
        selected_departments=_list_value(data.get("selected_departments")),
        custom_departments=_list_value(data.get("custom_departments")),
        org_notes=str(data.get("org_notes") or "").strip(),
        confirmation_notes=str(data.get("confirmation_notes") or "").strip(),
        recommended_members=_members_value(data.get("recommended_members")),
        source=source,
    )


def load_custom_templates(path: Path = CUSTOM_TEMPLATE_PATH) -> list[ProjectTemplate]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_templates = data.get("templates", data) if isinstance(data, dict) else data
    if not isinstance(raw_templates, list):
        return []
    templates: list[ProjectTemplate] = []
    for item in raw_templates:
        if isinstance(item, dict):
            template = template_from_dict(item, source="custom")
            if template.name:
                templates.append(template)
    return templates


def save_custom_templates(templates: list[ProjectTemplate], path: Path = CUSTOM_TEMPLATE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": "1.0", "templates": [template.to_dict() for template in templates if template.source == "custom"]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def all_templates() -> list[ProjectTemplate]:
    return BUILTIN_TEMPLATES + load_custom_templates()


def template_names() -> list[str]:
    return [template.name for template in all_templates()]


def get_template_by_name(name: str) -> ProjectTemplate | None:
    for template in all_templates():
        if template.name == name:
            return template
    return None


def get_template(template_id: str) -> ProjectTemplate | None:
    for template in all_templates():
        if template.id == template_id:
            return template
    return None


def is_custom_template(template: ProjectTemplate | None) -> bool:
    return bool(template and template.source == "custom")


def unique_template_name(base_name: str, current_name: str | None = None) -> str:
    base = str(base_name or "自定义模板").strip() or "自定义模板"
    existing = {template.name for template in all_templates() if template.name != current_name}
    if base not in existing:
        return base
    index = 2
    while f"{base} {index}" in existing:
        index += 1
    return f"{base} {index}"


def upsert_custom_template(data: dict[str, Any], original_name: str | None = None) -> ProjectTemplate:
    template = template_from_dict(data, source="custom")
    # Keep built-ins immutable. If a custom template is saved with a built-in name, make it a unique copy.
    builtin_names = {item.name for item in BUILTIN_TEMPLATES}
    if template.name in builtin_names and template.name != original_name:
        data = {**template.to_dict(), "name": unique_template_name(f"{template.name} 自定义", original_name)}
        template = template_from_dict(data, source="custom")
    custom_templates = load_custom_templates()
    replaced = False
    next_templates: list[ProjectTemplate] = []
    for item in custom_templates:
        if original_name and item.name == original_name:
            next_templates.append(template)
            replaced = True
        elif item.id == template.id or item.name == template.name:
            next_templates.append(template)
            replaced = True
        else:
            next_templates.append(item)
    if not replaced:
        if not original_name:
            template = template_from_dict({**template.to_dict(), "name": unique_template_name(template.name)}, source="custom")
        next_templates.append(template)
    save_custom_templates(next_templates)
    return template


def delete_custom_template(name: str) -> bool:
    custom_templates = load_custom_templates()
    next_templates = [template for template in custom_templates if template.name != name]
    if len(next_templates) == len(custom_templates):
        return False
    save_custom_templates(next_templates)
    return True


def export_template(template: ProjectTemplate, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": "1.0", "template": template.to_dict()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def import_template(path: Path) -> ProjectTemplate:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("template"), dict):
        data = data["template"]
    elif isinstance(data, dict) and isinstance(data.get("templates"), list) and data["templates"]:
        data = data["templates"][0]
    if not isinstance(data, dict):
        raise ValueError("模板文件格式无效。")
    imported = template_from_dict(data, source="custom")
    imported_data = imported.to_dict()
    imported_data["name"] = unique_template_name(imported.name)
    imported_data["id"] = _slugify(imported_data["name"])
    return upsert_custom_template(imported_data)


def format_template_preview(template: ProjectTemplate | None) -> str:
    if template is None:
        return "请选择一个模板。"
    source_label = "自定义模板" if template.source == "custom" else "内置模板"
    lines = [
        f"{template.name}（{source_label}）",
        "",
        template.description or "暂无模板说明。",
        "",
        "建议启用部门：",
        "、".join(template.selected_departments) if template.selected_departments else "无",
        "",
        "建议新增部门：",
        "、".join(template.custom_departments) if template.custom_departments else "无",
        "",
        "建议插槽成员：",
    ]
    if template.recommended_members:
        for member in template.recommended_members:
            lines.append(f"- {member.get('name')}｜{member.get('level')}｜{member.get('department')}｜{member.get('position')}")
    else:
        lines.append("无")
    lines.extend(["", "组织备注：", template.org_notes or "无", "", "生成偏好：", template.confirmation_notes or "无"])
    return "\n".join(lines)


def template_to_edit_data(template: ProjectTemplate | None) -> dict[str, Any]:
    if template is None:
        return {
            "name": "",
            "description": "",
            "selected_departments": [],
            "custom_departments": [],
            "org_notes": "",
            "confirmation_notes": "",
            "recommended_members": [],
        }
    return template.to_dict()

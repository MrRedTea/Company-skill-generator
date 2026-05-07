from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from project_utils import (
    DEFAULT_DEPARTMENTS,
    DEPARTMENT_ROLE_MAP,
    ORG_LEVELS,
    SUPPORTED_COMPANY_FILE_TYPES,
    SUPPORTED_PERSONAL_FILE_TYPES,
    SUPPORTED_PERSONNEL_MODEL_FILE_TYPES,
    SUPPORTED_ORG_CHART_IMAGE_TYPES,
    detect_local_models,
    classify_employee_file_department,
    classify_employee_files_department,
    slugify,
)
from run import (
    OUTPUT_BASE,
    PREVIEW_DIR,
    build_request_from_legacy_args,
    build_runtime_settings,
    generate_preview_report,
    load_config,
    merge_request_defaults,
    run_company_skill,
)

app = FastAPI(
    title="算疏智合 API",
    description="公司技能生成器 V2.1 接口：支持资料上传、组织架构、组织人员蒸馏、预览确认与正式生成。",
    version="2.1",
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_BASE = BASE_DIR / "uploads"
logger = logging.getLogger(__name__)


def get_project_config() -> dict[str, Any]:
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        return load_config(str(config_path))
    return {}


class OrganizationPayload(BaseModel):
    selected_departments: list[str] = Field(default_factory=lambda: DEFAULT_DEPARTMENTS.copy())
    custom_departments: list[str] = Field(default_factory=list)
    org_notes: str = "参考大型公司常见组织架构的完整设置。"
    use_uploaded_org_chart: bool = False
    org_chart_files: list[str] = Field(default_factory=list)


class DepartmentAssignmentPayload(BaseModel):
    mode: str = "manual"
    detected_department: str = ""
    manual_department: str = ""
    final_department: str = ""
    confidence: float | None = None
    confidence_percent: float | None = None
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    candidate_scores: list[dict[str, Any]] = Field(default_factory=list)


class PersonalDistillationPayload(BaseModel):
    enabled: bool = False
    file_path: str = ""
    file_paths: list[str] = Field(default_factory=list)
    model_file_path: str = ""
    model_file_paths: list[str] = Field(default_factory=list)
    level: str = ""
    department: str = ""
    position: str = ""
    role_notes: str = ""
    has_peer_models: bool = True
    department_assignment: DepartmentAssignmentPayload = Field(default_factory=DepartmentAssignmentPayload)


class OrganizationMemberPayload(BaseModel):
    enabled: bool = True
    member_name: str = ""
    member_type: str = "组织成员"
    level: str = ""
    department: str = ""
    position: str = ""
    role_notes: str = ""
    has_peer_models: bool = True
    profile_file_path: str = ""
    profile_file_paths: list[str] = Field(default_factory=list)
    skill_file_path: str = ""
    skill_file_paths: list[str] = Field(default_factory=list)
    file_path: str = ""
    file_paths: list[str] = Field(default_factory=list)
    model_file_path: str = ""
    model_file_paths: list[str] = Field(default_factory=list)
    department_assignment: DepartmentAssignmentPayload = Field(default_factory=DepartmentAssignmentPayload)
    insert_mode: str = "append_non_destructive"
    authority_level: str = "advisory"


class GenerationRequestV21(BaseModel):
    version: str = "2.1"
    company_name: str = ""
    company_slug: str = ""
    model_type: Literal["cloud", "local"] = "cloud"
    provider: Literal["deepseek", "openai", "其他"] = "deepseek"
    api_key: str | None = None
    api_base: str | None = None
    model: str | None = None
    local_model_type: str | None = None
    local_model_name: str | None = None
    local_host: str | None = None
    temperature: float | str | None = None
    company_files: list[str] = Field(default_factory=list)
    organization: OrganizationPayload = Field(default_factory=OrganizationPayload)
    personal_distillation: PersonalDistillationPayload = Field(default_factory=PersonalDistillationPayload)
    organization_members: list[OrganizationMemberPayload] = Field(default_factory=list)
    confirmation_notes: str = ""


class LegacyGenerationRequest(BaseModel):
    company_slug: str
    user_query: str
    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    local: bool = False
    local_model: str | None = None
    local_host: str | None = None
    temperature: float | str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: str
    result_path: str | None = None
    preview_path: str | None = None
    files: list[str] = Field(default_factory=list)
    error: str | None = None


TASK_STATUS: dict[str, dict[str, Any]] = {}

ALLOWED_OUTPUT_FILES = {
    "boss-skill.md",
    "employee-collective-skill.md",
    "company-code.md",
    "SKILL.md",
    "strategy-vision.md",
    "career-path.md",
    "organization-members.md",
    "generation-request.json",
    "generation-summary.md",
}

ALLOWED_PREVIEW_SUFFIXES = {"-preview-report.md", "-preview-report.json"}


def namespace_for_payload(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        model=payload.get("model"),
        api_key=payload.get("api_key"),
        api_base=payload.get("api_base"),
        local=(payload.get("model_type") == "local"),
        local_model=payload.get("local_model_name"),
        local_host=payload.get("local_host"),
        temperature=payload.get("temperature"),
    )


def normalize_v21_request(request: GenerationRequestV21) -> dict[str, Any]:
    payload = request.model_dump()
    if not payload.get("company_slug"):
        payload["company_slug"] = slugify(payload.get("company_name") or "company-skill")
    return merge_request_defaults(payload)


def normalize_legacy_request(request: LegacyGenerationRequest, config: dict[str, Any]) -> dict[str, Any]:
    raw = request.model_dump()
    args = SimpleNamespace(company_slug=raw["company_slug"], user_query=raw["user_query"], local=raw.get("local", False))
    payload = build_request_from_legacy_args(args, config)
    if raw.get("api_key"):
        payload["api_key"] = raw["api_key"]
    if raw.get("api_base"):
        payload["api_base"] = raw["api_base"]
    if raw.get("model"):
        payload["model"] = raw["model"]
    if raw.get("local_model"):
        payload["local_model_name"] = raw["local_model"]
    if raw.get("local_host"):
        payload["local_host"] = raw["local_host"]
    if raw.get("temperature") is not None:
        payload["temperature"] = raw["temperature"]
    return merge_request_defaults(payload)


def build_settings(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    settings = build_runtime_settings(namespace_for_payload(payload), config, payload)
    if not settings.get("api_key"):
        raise HTTPException(status_code=400, detail="缺少 API Key。云端模式请在请求体、config.json 或环境变量中提供；本地模式会自动使用 local-mode。")
    return settings


def run_generation_task(task_id: str, payload: dict[str, Any], settings: dict[str, Any]) -> None:
    try:
        TASK_STATUS[task_id].update({"status": "running", "progress": "开始生成公司技能文件..."})
        out_dir = run_company_skill(payload, settings)
        files = sorted([item.name for item in out_dir.iterdir() if item.is_file()])
        TASK_STATUS[task_id].update({
            "status": "completed",
            "progress": "生成完成",
            "result_path": str(out_dir),
            "files": files,
        })
    except Exception as exc:
        logger.exception("后台生成任务失败")
        TASK_STATUS[task_id].update({
            "status": "failed",
            "progress": "生成失败",
            "error": str(exc),
        })


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "message": "算疏智合 API v0.1.0",
        "endpoints": {
            "GET /health": "健康检查",
            "GET /organization/template": "返回组织架构模板与支持格式",
            "GET /local-models/detect": "自动识别 Ollama / LM Studio / OpenAI 兼容本地模型",
            "POST /personnel/classify-department": "自动识别员工资料所属部门",
            "POST /upload": "上传资料文件并返回服务端路径",
            "POST /preview": "基于 V2.1 配置生成预览确认报告",
            "POST /generate": "基于 V2.1 配置发起正式生成任务",
            "POST /generate-sync": "同步执行正式生成，适合本地联调或本地部署调用",
            "POST /generate-legacy": "兼容旧版 company_slug + user_query 接口",
            "GET /status/{task_id}": "查询后台生成任务状态",
            "GET /result/{company_slug}": "查看某次生成的结果文件列表",
            "GET /download/{company_slug}/{filename}": "下载正式输出文件",
            "GET /download-preview/{filename}": "下载预览确认报告",
        },
    }


class DepartmentClassificationRequest(BaseModel):
    file_path: str = ""
    file_paths: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)


@app.post("/personnel/classify-department")
async def classify_personnel_department(request: DepartmentClassificationRequest) -> dict[str, Any]:
    raw_paths = request.file_paths or ([request.file_path] if request.file_path else [])
    file_paths = [Path(path) for path in raw_paths if path]
    if not file_paths:
        raise HTTPException(status_code=400, detail="请至少提供一个员工资料文件。")
    missing = [str(path) for path in file_paths if not path.exists()]
    if missing:
        raise HTTPException(status_code=404, detail=f"员工资料文件不存在：{', '.join(missing)}")
    candidates = request.departments or DEFAULT_DEPARTMENTS
    if len(file_paths) == 1:
        return classify_employee_file_department(file_paths[0], candidates=candidates)
    return classify_employee_files_department([str(path) for path in file_paths], candidates=candidates)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "2.1"}


@app.get("/local-models/detect")
async def detect_local_model_endpoint(local_host: str | None = None, local_model_type: str | None = None) -> dict[str, Any]:
    models = detect_local_models(local_host=local_host, local_model_type=local_model_type)
    return {"count": len(models), "models": models}


@app.get("/organization/template")
async def organization_template() -> dict[str, Any]:
    return {
        "version": "2.1",
        "default_departments": DEFAULT_DEPARTMENTS,
        "org_levels": ORG_LEVELS,
        "department_role_map": DEPARTMENT_ROLE_MAP,
        "supported_company_file_types": SUPPORTED_COMPANY_FILE_TYPES,
        "supported_personal_file_types": SUPPORTED_PERSONAL_FILE_TYPES,
        "supported_providers": ["deepseek", "openai", "其他"],
        "output_files": sorted(ALLOWED_OUTPUT_FILES),
    }


@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件。")
    bucket = uuid.uuid4().hex[:12]
    save_dir = UPLOAD_BASE / bucket
    save_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    allowed = set(SUPPORTED_COMPANY_FILE_TYPES) | set(SUPPORTED_PERSONAL_FILE_TYPES) | set(SUPPORTED_PERSONNEL_MODEL_FILE_TYPES)
    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix and suffix not in allowed:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{upload.filename}")
        filename = Path(upload.filename or f"upload-{uuid.uuid4().hex}").name
        dest = save_dir / filename
        with dest.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        saved.append({"filename": filename, "path": str(dest), "size": dest.stat().st_size})
    return {"bucket": bucket, "count": len(saved), "files": saved}


@app.post("/preview")
async def preview_company_skill(request: GenerationRequestV21) -> dict[str, Any]:
    config = get_project_config()
    payload = normalize_v21_request(request)
    settings = build_settings(payload, config)
    preview_md, preview_json = generate_preview_report(payload, settings)
    return {
        "message": "预览确认报告生成完成",
        "company_slug": payload["company_slug"],
        "preview_markdown_path": str(preview_md),
        "preview_json_path": str(preview_json),
    }


@app.post("/generate", response_model=dict)
async def generate_company_skill(request: GenerationRequestV21, background_tasks: BackgroundTasks) -> dict[str, Any]:
    config = get_project_config()
    payload = normalize_v21_request(request)
    settings = build_settings(payload, config)
    task_id = str(uuid.uuid4())
    TASK_STATUS[task_id] = {
        "status": "queued",
        "progress": "任务已创建，等待后台执行",
        "result_path": None,
        "preview_path": None,
        "files": [],
        "error": None,
    }
    background_tasks.add_task(run_generation_task, task_id, payload, settings)
    return {"task_id": task_id, "message": "V2.1 生成任务已启动，请轮询 /status/{task_id}"}




@app.post("/generate-sync")
async def generate_company_skill_sync(request: GenerationRequestV21) -> dict[str, Any]:
    config = get_project_config()
    payload = normalize_v21_request(request)
    settings = build_settings(payload, config)
    out_dir = run_company_skill(payload, settings)
    files = sorted([item.name for item in out_dir.iterdir() if item.is_file()])
    return {
        "message": "V2.1 同步生成完成",
        "company_slug": payload["company_slug"],
        "result_path": str(out_dir),
        "files": files,
    }


@app.post("/generate-legacy", response_model=dict)
async def generate_company_skill_legacy(request: LegacyGenerationRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    config = get_project_config()
    payload = normalize_legacy_request(request, config)
    settings = build_settings(payload, config)
    task_id = str(uuid.uuid4())
    TASK_STATUS[task_id] = {
        "status": "queued",
        "progress": "已将旧版请求转换为 V2.1 任务",
        "result_path": None,
        "preview_path": None,
        "files": [],
        "error": None,
    }
    background_tasks.add_task(run_generation_task, task_id, payload, settings)
    return {"task_id": task_id, "message": "旧版请求已转换为 V2.1 生成任务，请轮询 /status/{task_id}"}


@app.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_generation_status(task_id: str) -> TaskStatusResponse:
    if task_id not in TASK_STATUS:
        raise HTTPException(status_code=404, detail="任务不存在")
    status = TASK_STATUS[task_id]
    return TaskStatusResponse(task_id=task_id, **status)


@app.get("/result/{company_slug}")
async def get_generation_result(company_slug: str) -> dict[str, Any]:
    out_dir = OUTPUT_BASE / company_slug
    if not out_dir.exists() or not out_dir.is_dir():
        raise HTTPException(status_code=404, detail="结果目录不存在")
    files = sorted([item.name for item in out_dir.iterdir() if item.is_file()])
    return {"company_slug": company_slug, "result_path": str(out_dir), "files": files}


@app.get("/download/{company_slug}/{filename}")
async def download_file(company_slug: str, filename: str) -> FileResponse:
    if filename not in ALLOWED_OUTPUT_FILES:
        raise HTTPException(status_code=400, detail="不允许下载该文件")
    file_path = OUTPUT_BASE / company_slug / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = "application/json" if file_path.suffix == ".json" else "text/markdown"
    return FileResponse(path=file_path, filename=filename, media_type=media_type)


@app.get("/download-preview/{filename}")
async def download_preview_file(filename: str) -> FileResponse:
    if not any(filename.endswith(suffix) for suffix in ALLOWED_PREVIEW_SUFFIXES):
        raise HTTPException(status_code=400, detail="不允许下载该预览文件")
    safe_name = Path(filename).name
    file_path = PREVIEW_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="预览文件不存在")
    media_type = "application/json" if file_path.suffix == ".json" else "text/markdown"
    return FileResponse(path=file_path, filename=safe_name, media_type=media_type)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

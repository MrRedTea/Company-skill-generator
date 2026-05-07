# -*- coding: utf-8 -*-
"""Background workers for the PySide6 GUI.

Long-running and network-facing tasks stay outside the main GUI thread so the
main window remains focused on orchestration and page state.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from project_utils import (
    classify_employee_file_department,
    classify_employee_files_department,
    detect_local_models,
    scan_member_directories,
)


class BatchMemberImportWorker(QObject):
    progress = Signal(str)
    finished = Signal(object, str)

    def __init__(self, root_dir: str, candidates: list[str], default_level: str, default_department: str):
        super().__init__()
        self.root_dir = root_dir
        self.candidates = candidates
        self.default_level = default_level
        self.default_department = default_department

    def run(self):
        try:
            result = scan_member_directories(
                self.root_dir,
                candidates=self.candidates,
                default_level=self.default_level,
                default_department=self.default_department,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(result, "")
        except Exception as exc:  # pragma: no cover - runtime path
            self.finished.emit({}, str(exc))

class LocalModelWorker(QObject):
    finished = Signal(object, str)

    def __init__(self, host: str, model_type: str):
        super().__init__()
        self.host = host
        self.model_type = model_type

    def run(self):
        try:
            models = detect_local_models(local_host=self.host, local_model_type=self.model_type)
            self.finished.emit(models, "")
        except Exception as exc:  # pragma: no cover - runtime path
            self.finished.emit([], str(exc))


class ModelConnectionTestWorker(QObject):
    """Connection tester with cancellation and strict time limits.

    The connection check is fail-fast and chat-first. The generated Skill flow
    ultimately relies on an OpenAI-compatible chat/completions endpoint, so the
    tester validates that endpoint directly instead of depending on /models.
    """

    finished = Signal(bool, str)
    status = Signal(str)

    TOTAL_TIMEOUT_SECONDS = 25
    PER_REQUEST_TIMEOUT_SECONDS = 6

    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload
        self._cancel_requested = False
        self._started_at = 0.0

    def cancel(self):
        self._cancel_requested = True

    def _cancelled_or_timeout(self) -> bool:
        if self._cancel_requested:
            return True
        if not self._started_at:
            return False
        return (time.monotonic() - self._started_at) >= self.TOTAL_TIMEOUT_SECONDS

    def _remaining_timeout(self) -> int:
        if not self._started_at:
            return self.PER_REQUEST_TIMEOUT_SECONDS
        remaining = self.TOTAL_TIMEOUT_SECONDS - (time.monotonic() - self._started_at)
        return max(2, min(self.PER_REQUEST_TIMEOUT_SECONDS, int(remaining)))

    @staticmethod
    def _clean_base(api_base: str) -> str:
        base = (api_base or "").strip().rstrip("/")
        for suffix in ("/chat/completions", "/models"):
            if base.endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
        return base

    @staticmethod
    def _base_candidates(api_base: str, provider: str = "") -> list[str]:
        base = ModelConnectionTestWorker._clean_base(api_base)
        if not base:
            return []
        provider = (provider or "").lower()
        candidates: list[str] = []

        def add(value: str) -> None:
            value = (value or "").rstrip("/")
            if value and value not in candidates:
                candidates.append(value)

        # DeepSeek's documented OpenAI-compatible base is https://api.deepseek.com.
        # OpenAI normally uses /v1.  Custom providers vary, so we try both but keep
        # the user-provided base first to avoid slow or wrong fallbacks.
        if provider == "openai" and not base.endswith("/v1"):
            add(f"{base}/v1")
            add(base)
        else:
            add(base)
            if base.endswith("/v1"):
                add(base[:-3].rstrip("/"))
            else:
                add(f"{base}/v1")
        return candidates[:2]

    @staticmethod
    def _read_http_error(exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read().decode("utf-8", errors="replace")[:360]
        except Exception:
            return ""

    @staticmethod
    def _json_request(method: str, url: str, api_key: str, payload: dict | None = None, timeout: int = 6) -> tuple[int, dict | list | str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read().decode("utf-8", errors="replace")
            if not raw:
                return status, {}
            try:
                return status, json.loads(raw)
            except Exception:
                return status, raw[:360]

    @classmethod
    def _looks_like_chat_success(cls, body: dict | list | str) -> bool:
        if isinstance(body, dict):
            if isinstance(body.get("choices"), list) and body["choices"]:
                return True
            if body.get("id") and body.get("model"):
                return True
        return False

    def _try_chat_endpoint(self, bases: list[str], api_key: str, model: str) -> tuple[bool, str, str | None]:
        errors: list[str] = []
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        for base in bases:
            if self._cancelled_or_timeout():
                return False, "连接检查已取消或超时。", None
            endpoint = f"{base}/chat/completions"
            self.status.emit(f"正在检查聊天补全接口：{base}")
            try:
                status, body = self._json_request("POST", endpoint, api_key, payload=payload, timeout=self._remaining_timeout())
                if 200 <= status < 300 and self._looks_like_chat_success(body):
                    return True, f"聊天补全接口可用：{base}/chat/completions", base
                if 200 <= status < 300:
                    return True, f"接口返回成功，但返回体未完全匹配标准 OpenAI 格式：{base}", base
                errors.append(f"{endpoint} 返回状态码 {status}")
            except urllib.error.HTTPError as exc:
                detail = self._read_http_error(exc)
                if exc.code in (401, 403):
                    return False, "接口可达，但 API Key 无效或权限不足。", None
                if exc.code == 404:
                    errors.append(f"{endpoint} 返回 404")
                    continue
                if exc.code in (400, 422):
                    errors.append(f"{endpoint} 拒绝当前模型或参数：HTTP {exc.code} {detail}".strip())
                else:
                    errors.append(f"{endpoint} 返回 HTTP {exc.code} {detail}".strip())
            except TimeoutError:
                errors.append(f"{endpoint} 请求超时")
            except Exception as exc:
                errors.append(f"{endpoint} 访问失败：{exc}")
        return False, "；".join(errors[:3]) or "聊天补全接口检查失败。", None

    def _try_models_endpoint_optional(self, base: str, api_key: str) -> str:
        if not base or self._cancelled_or_timeout():
            return ""
        endpoint = f"{base}/models"
        try:
            status, body = self._json_request("GET", endpoint, api_key, timeout=min(4, self._remaining_timeout()))
            if 200 <= status < 300:
                if isinstance(body, dict) and isinstance(body.get("data"), list):
                    return f"模型列表可访问，模型数：{len(body.get('data') or [])}。"
                return "模型列表接口可访问。"
        except Exception:
            return ""
        return ""

    def run(self):
        self._started_at = time.monotonic()
        try:
            model_type = self.payload.get("model_type")
            if model_type == "local":
                host = str(self.payload.get("local_host") or "").strip()
                local_model_type = str(self.payload.get("local_model_type") or "ollama").strip()
                expected_model = str(self.payload.get("local_model_name") or "").strip()
                if not host:
                    self.finished.emit(False, "请先填写本地模型服务地址。")
                    return
                self.status.emit("正在检测本地模型服务...")
                models = detect_local_models(local_host=host, local_model_type=local_model_type)
                if self._cancel_requested:
                    self.finished.emit(False, "模型连接检查已取消。")
                    return
                names = [str(item.get("model_name") or item.get("name") or "") for item in models]
                if expected_model and names and expected_model not in names:
                    self.finished.emit(True, f"本地服务可连接，但未在模型列表中看到 {expected_model}。已识别：{', '.join(names[:5])}")
                    return
                self.finished.emit(True, f"本地模型服务连接成功。识别模型数：{len(models)}。")
                return

            provider = str(self.payload.get("provider") or "").strip().lower()
            api_base = str(self.payload.get("api_base") or "").strip()
            api_key = str(self.payload.get("api_key") or "").strip()
            model = str(self.payload.get("model") or "").strip()
            if not api_base:
                self.finished.emit(False, "请先填写 API Base。")
                return
            if not api_key:
                self.finished.emit(False, "请先填写 API Key；如果使用本地模型，请切换到本地模式。")
                return
            if not model:
                self.finished.emit(False, "请先填写模型名称。")
                return

            bases = self._base_candidates(api_base, provider)
            if not bases:
                self.finished.emit(False, "API Base 格式无效。")
                return

            self.status.emit("正在用当前模型发起最小聊天补全检查...")
            chat_ok, chat_note, chat_base = self._try_chat_endpoint(bases, api_key, model)
            if self._cancel_requested:
                self.finished.emit(False, "模型连接检查已取消。")
                return
            if self._cancelled_or_timeout():
                self.finished.emit(False, "模型连接检查超时，请检查网络、代理、API Base 或模型服务状态。")
                return
            if chat_ok:
                optional_note = self._try_models_endpoint_optional(chat_base or bases[0], api_key)
                suffix = f" {optional_note}" if optional_note else ""
                self.finished.emit(True, f"模型连接检查通过。当前模型 {model} 可调用。{suffix}")
                return
            self.finished.emit(False, f"模型连接检查失败：{chat_note}")
        except Exception as exc:  # pragma: no cover - runtime path
            self.finished.emit(False, str(exc))

class DepartmentDetectWorker(QObject):
    progress = Signal(str)
    finished = Signal(object, str)

    def __init__(self, file_paths: list[str], candidates: list[str]):
        super().__init__()
        self.file_paths = file_paths
        self.candidates = candidates

    def run(self):
        try:
            if len(self.file_paths) == 1:
                self.progress.emit(f"正在解析员工资料：{Path(self.file_paths[0]).name}")
                result = classify_employee_file_department(self.file_paths[0], candidates=self.candidates)
            else:
                result = classify_employee_files_department(
                    self.file_paths,
                    candidates=self.candidates,
                    progress_callback=self.progress.emit,
                )
            self.finished.emit(result, "")
        except Exception as exc:  # pragma: no cover - runtime path
            self.finished.emit({}, str(exc))


class SubprocessWorker(QObject):
    log = Signal(str)
    progress = Signal(str, int)
    finished = Signal(str, object, int, str)

    def __init__(self, cmd: list[str], mode: str, cwd: Path, timeout_seconds: int | None = None):
        super().__init__()
        self.cmd = cmd
        self.mode = mode
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen | None = None
        self._cancel_requested = False

    def cancel_process(self):
        self._cancel_requested = True
        try:
            if self.process and self.process.poll() is None:
                self.process.kill()
        except Exception:
            pass

    def run(self):
        output_lines: list[str] = []
        try:
            masked_cmd = []
            hide_next = False
            for part in self.cmd:
                if hide_next:
                    masked_cmd.append("***")
                    hide_next = False
                    continue
                masked_cmd.append(part)
                if part == "--api-key":
                    hide_next = True
            self.log.emit(f"📋 执行命令：{' '.join(masked_cmd)}")
            start_label = "准备启动生成进程"
            running_label = "读取资料并调用模型"
            done_label = "写入输出文件"
            self.progress.emit(start_label, 8)

            child_env = os.environ.copy()
            child_env.setdefault("PYTHONIOENCODING", "utf-8")
            child_env.setdefault("PYTHONUTF8", "1")
            self.process = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
                cwd=str(self.cwd),
            )
            self.progress.emit(running_label, 20)
            try:
                output, _ = self.process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self.cancel_process()
                try:
                    output, _ = self.process.communicate(timeout=5)
                except Exception:
                    output = ""
                output_lines.extend([line.strip() for line in (output or "").splitlines() if line.strip()])
                timeout_text = f"任务超过 {self.timeout_seconds} 秒仍未结束，已自动取消。请检查模型服务、API Base、API Key 或网络连接。"
                self.finished.emit(self.mode, output_lines, -2, timeout_text)
                return

            output_lines.extend([line.strip() for line in (output or "").splitlines() if line.strip()])
            for line in output_lines:
                self.log.emit(line)
            if self._cancel_requested:
                self.finished.emit(self.mode, output_lines, -3, "任务已取消。")
                return
            self.progress.emit(done_label, 88)
            self.finished.emit(self.mode, output_lines, int(self.process.poll() or 0), "")
        except Exception as exc:  # pragma: no cover - runtime path
            self.finished.emit(self.mode, output_lines, -1, str(exc))



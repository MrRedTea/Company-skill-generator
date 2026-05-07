# -*- coding: utf-8 -*-
"""Modern PySide6 GUI for 算疏智合.

Provides model configuration, material upload, organization setup, member import,
generation, output inspection and quality checks in one desktop workflow.
"""
from __future__ import annotations

import json
import locale
import os
import re
import threading
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from PySide6.QtCore import QEvent, QPoint, QObject, Qt, QThread, QTimer, Signal, QUrl
    from PySide6.QtGui import QIcon, QDesktopServices
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QPushButton,
        QProgressBar,
        QRadioButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - user-facing runtime guard
    PYSIDE_IMPORT_ERROR = exc
else:  # pragma: no cover - import guard bookkeeping
    PYSIDE_IMPORT_ERROR = None

if PYSIDE_IMPORT_ERROR is not None:  # pragma: no cover - user-facing runtime guard
    print("❌ PySide6 未安装，无法启动 Qt GUI。")
    print("请先执行：pip install -r requirements.txt")
    print(f"原始错误：{PYSIDE_IMPORT_ERROR}")
    legacy = Path(__file__).with_name("gui_legacy_tkinter.py")
    if legacy.exists():
        print("如需使用旧版界面，可运行：python gui_legacy_tkinter.py")
    sys.exit(1)

from project_utils import (
    DEFAULT_DEPARTMENTS,
    DEPARTMENT_ROLE_MAP,
    ORG_LEVELS,
    SUPPORTED_COMPANY_FILE_TYPES,
    SUPPORTED_PERSONAL_FILE_TYPES,
    SUPPORTED_PERSONNEL_MODEL_FILE_TYPES,
    classify_employee_file_department,
    classify_employee_files_department,
    detect_local_models,
    format_file_names,
    looks_like_org_chart_file,
    merge_file_path_values,
    scan_member_directories,
    slugify,
)

from ui.branding import APP_NAME, APP_TITLE, ROUND, VERSION
from ui.dialogs import ThemeMessageDialog, TemplateEditorDialog
from ui.output_quality import format_quality_report, inspect_output_quality
from ui.generation_history import (
    append_generation_record,
    format_history_detail,
    format_history_item,
    load_generation_history,
)
from ui.config_health import format_health_report, run_config_health_check
from ui.app_logging import append_task_log, create_task_log_path, setup_app_logging
from ui.app_settings import AppSettings
from ui.project_store import default_project_filename, load_project_file, save_project_file
from ui.templates import (
    all_templates,
    delete_custom_template,
    export_template,
    format_template_preview,
    get_template_by_name,
    import_template,
    is_custom_template,
    template_to_edit_data,
    unique_template_name,
    upsert_custom_template,
)
from ui.theme import COLORS, apply_app_theme, apply_soft_shadow
from ui.workers import (
    BatchMemberImportWorker,
    DepartmentDetectWorker,
    LocalModelWorker,
    ModelConnectionTestWorker,
    SubprocessWorker,
)

CONFIG_PATH = Path("config.json")

INVALID_COMPANY_SLUGS = {"", "无", "none", "null", "nil", "n/a", "na", "未填写", "默认", "default", "company", "company-skill", "公司"}
AUTO_DETECT_PERSONNEL_FILE_LIMIT = 8
DEPARTMENT_DETECTION_TIMEOUT_MS = 90_000
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{12,}", flags=re.I),
)

def is_invalid_company_slug(value: object) -> bool:
    return str(value or "").strip().lower() in INVALID_COMPANY_SLUGS


def normalize_company_slug(raw_slug: object, company_name: object) -> str:
    candidate = slugify(str(raw_slug or "")) if raw_slug else ""
    if is_invalid_company_slug(candidate):
        candidate = slugify(str(company_name or ""))
    if is_invalid_company_slug(candidate):
        candidate = "company-skill"
    return candidate


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in ("api_key", "secret", "password", "token", "authorization")):
                redacted[key] = "***REDACTED***" if item else item
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern in SECRET_VALUE_PATTERNS:
            text = pattern.sub("***REDACTED***", text)
        return text
    return value


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


def load_json_with_encoding_fallback(path: Path) -> dict:
    return json.loads(read_text_with_encoding_fallback(path))


def file_filter(title: str, suffixes: tuple[str, ...]) -> str:
    pattern = " ".join(f"*{suffix}" for suffix in suffixes)
    return f"{title} ({pattern});;所有文件 (*.*)"


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        child = item.widget()
        if child is not None:
            child.deleteLater()


class WindowTitleBar(QFrame):
    """Custom embedded title bar for the frameless Qt window."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(52)
        self._drag_offset: QPoint | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 8, 6)
        layout.setSpacing(8)

        self.app_mark = QLabel("●")
        self.app_mark.setObjectName("TitleMark")
        layout.addWidget(self.app_mark)

        self.title_label = QLabel(APP_TITLE)
        self.title_label.setObjectName("TitleBarText")
        layout.addWidget(self.title_label, 1)

        self.minimize_btn = QPushButton("—")
        self.minimize_btn.setObjectName("WindowButton")
        self.minimize_btn.clicked.connect(lambda: self.window().showMinimized())
        layout.addWidget(self.minimize_btn)

        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setObjectName("WindowButton")
        self.maximize_btn.clicked.connect(self.toggle_maximize_restore)
        layout.addWidget(self.maximize_btn)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseButton")
        self.close_btn.clicked.connect(lambda: self.window().close())
        layout.addWidget(self.close_btn)

        for button in (self.minimize_btn, self.maximize_btn, self.close_btn):
            button.setFixedSize(38, 30)
            button.setCursor(Qt.CursorShape.ArrowCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def toggle_maximize_restore(self):
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        self.sync_window_state()

    def sync_window_state(self):
        if self.window().isMaximized():
            self.maximize_btn.setText("❐")
        else:
            self.maximize_btn.setText("□")

    def mouseDoubleClickEvent(self, event):  # pragma: no cover - GUI behavior
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):  # pragma: no cover - GUI behavior
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            started = False
            if handle is not None and not window.isMaximized():
                try:
                    started = bool(handle.startSystemMove())
                except Exception:
                    started = False
            if not started and not window.isMaximized():
                self._drag_offset = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # pragma: no cover - GUI behavior
        window = self.window()
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton and not window.isMaximized():
            window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # pragma: no cover - GUI behavior
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class ModernCompanySkillGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)
        self.resize(1480, 920)
        self.setMinimumSize(1180, 760)
        self.setWindowIcon(QIcon())
        self.logger = setup_app_logging(APP_NAME)
        self.app_settings = AppSettings(APP_NAME)
        self.current_task_log_path: Path | None = None
        self.last_task_log_path: Path | None = None
        self.current_project_path: Path | None = None

        self.steps = ["模型配置", "资料上传", "组织架构", "组织人员蒸馏", "生成确认"]
        self.current_step = 0
        self.company_files: list[str] = []
        self.custom_departments_list: list[str] = []
        self.org_chart_files: list[str] = []
        self.organization_members: list[dict] = []
        self.editing_member_index: int | None = None
        self.employee_department_detection: dict = {}
        self.detected_local_models: list[dict[str, str]] = []
        self.preview_md_path: Path | None = None
        self.preview_json_path: Path | None = None
        self.current_output_dir: Path | None = None
        self.generation_history_records: list[dict[str, Any]] = []
        self._threads: list[QThread] = []
        self._workers: list[QObject] = []
        self._python_threads: list[Any] = []
        self._dialogs: list[QDialog] = []
        self.active_model_test_worker: ModelConnectionTestWorker | None = None
        self.active_model_test_thread: Any | None = None
        self.model_test_run_id = 0
        self.last_model_test_ok: bool | None = None
        self.last_model_test_message = ""
        self.model_test_timeout_timer = QTimer(self)
        self.model_test_timeout_timer.setSingleShot(True)
        self.model_test_timeout_timer.timeout.connect(self.handle_model_test_timeout)
        self.department_detection_run_id = 0
        self._applying_auto_department = False
        self.log_history: list[str] = []
        self.active_task_mode: str | None = None
        self.active_task_started_at: datetime | None = None
        self.active_worker: SubprocessWorker | None = None
        self.task_progress_value = 0
        self.task_progress_timer = QTimer(self)
        self.task_progress_timer.setInterval(850)
        self.task_progress_timer.timeout.connect(self.advance_task_progress)
        self.resize_margin = 8

        self.init_style()
        self.build_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.load_config()
        self.restore_app_state()
        self.refresh_recent_projects_list()
        self.refresh_generation_history()
        self.update_project_label()
        self.show_step(0)
        self.log_message(f"🚀 已启动 {APP_TITLE}")

    def init_style(self):
        apply_app_theme(self)

    def apply_soft_shadow(self, widget: QWidget, blur_radius: int = 28, y_offset: int = 8):
        apply_soft_shadow(widget, blur_radius=blur_radius, y_offset=y_offset)

    def show_error_dialog(self, title: str, message: str):
        self.open_message_dialog("error", title, message, [("知道了", True)], subtitle="请检查后重试")

    def show_info_dialog(self, title: str, message: str):
        self.open_message_dialog("info", title, message, [("知道了", True)])

    def show_confirm_dialog(self, title: str, message: str, confirm_text: str = "继续", cancel_text: str = "取消") -> bool:
        return ThemeMessageDialog.show(self, "question", title, message, [(cancel_text, False), (confirm_text, True)], subtitle="确认后继续执行")

    def open_message_dialog(self, kind: str, title: str, message: str, buttons: list[tuple[str, bool]] | None = None, subtitle: str | None = None):
        """Show non-blocking info/error dialogs and keep them alive safely.

        This avoids crashes that can happen when a result dialog is opened from a
        worker-thread completion slot and then closed while QThread cleanup events are
        still being delivered.
        """
        dialog = ThemeMessageDialog.open_non_blocking(self, kind, title, message, buttons, subtitle)
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda _result=0, dlg=dialog: self._cleanup_dialog(dlg))
        return dialog

    def _cleanup_dialog(self, dialog: QDialog):
        try:
            self._dialogs.remove(dialog)
        except ValueError:
            pass
        dialog.deleteLater()

    def build_ui(self):
        root = QWidget()
        root.setObjectName("AppShell")
        self.setCentralWidget(root)
        outer_layout = QVBoxLayout(root)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.setSpacing(10)

        self.title_bar = WindowTitleBar(self)
        outer_layout.addWidget(self.title_bar)

        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(4, 0, 4, 4)
        root_layout.setSpacing(16)
        outer_layout.addLayout(root_layout, 1)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(238)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(18, 18, 18, 16)
        side.setSpacing(10)

        brand = QLabel("算疏智合")
        brand.setObjectName("BrandTitle")
        side.addWidget(brand)
        sub = QLabel("企业 Skill 工作台\nPySide6 / Qt Widgets")
        sub.setObjectName("BrandSubtitle")
        side.addWidget(sub)

        project_section = QLabel("项目管理")
        project_section.setObjectName("SidebarSection")
        side.addWidget(project_section)

        project_row_1 = QHBoxLayout()
        project_row_1.setSpacing(8)
        self.new_project_btn = QPushButton("新建")
        self.open_project_btn = QPushButton("打开")
        self.new_project_btn.clicked.connect(self.new_project)
        self.open_project_btn.clicked.connect(self.open_project_dialog)
        project_row_1.addWidget(self.new_project_btn)
        project_row_1.addWidget(self.open_project_btn)
        side.addLayout(project_row_1)

        project_row_2 = QHBoxLayout()
        project_row_2.setSpacing(8)
        self.save_project_btn = QPushButton("保存")
        self.save_project_as_btn = QPushButton("另存")
        self.save_project_btn.setProperty("variant", "accent")
        self.save_project_btn.clicked.connect(self.save_project)
        self.save_project_as_btn.clicked.connect(self.save_project_as)
        project_row_2.addWidget(self.save_project_btn)
        project_row_2.addWidget(self.save_project_as_btn)
        side.addLayout(project_row_2)

        self.current_project_label = QLabel("当前项目：未保存")
        self.current_project_label.setObjectName("SmallHint")
        self.current_project_label.setWordWrap(True)
        side.addWidget(self.current_project_label)

        self.recent_project_list = QListWidget()
        self.recent_project_list.setObjectName("RecentProjectList")
        self.recent_project_list.setFixedHeight(82)
        self.recent_project_list.itemDoubleClicked.connect(self.open_recent_project_item)
        side.addWidget(self.recent_project_list)

        section = QLabel("流程导航")
        section.setObjectName("SidebarSection")
        side.addWidget(section)

        self.step_buttons: list[QPushButton] = []
        for index, name in enumerate(self.steps):
            btn = QPushButton(f"{index + 1}. {name}")
            btn.setMinimumHeight(48)
            btn.setProperty("step", "pending")
            btn.clicked.connect(lambda checked=False, idx=index: self.goto_step(idx))
            self.step_buttons.append(btn)
            side.addWidget(btn)

        side.addStretch(1)
        # the sidebar free of lower-left status cards.
        self.status_label = QLabel("准备就绪")
        self.status_label.hide()

        root_layout.addWidget(self.sidebar)

        main = QVBoxLayout()
        main.setSpacing(13)
        root_layout.addLayout(main, 1)

        # content starts directly under the compact title bar.

        self.stack = QStackedWidget()
        self.stack.setObjectName("StepStack")
        main.addWidget(self.stack, 1)

        self.step_pages: list[QWidget] = []
        self.step_pages.append(self.create_step1())
        self.step_pages.append(self.create_step2())
        self.step_pages.append(self.create_step3())
        self.step_pages.append(self.create_step4())
        self.step_pages.append(self.create_step5())
        for page in self.step_pages:
            self.stack.addWidget(page)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("ActionBar")
        bottom = QHBoxLayout(self.action_bar)
        bottom.setContentsMargins(0, 2, 0, 0)
        bottom.setSpacing(10)
        self.prev_btn = QPushButton("‹ 上一步")
        self.prev_btn.clicked.connect(self.prev_step)
        self.next_btn = QPushButton("下一步 ›")
        self.next_btn.setProperty("variant", "accent")
        self.next_btn.clicked.connect(self.next_step)
        self.preview_btn = QPushButton("预览配置")
        self.preview_btn.setProperty("variant", "accent")
        self.preview_btn.clicked.connect(self.generate_preview)
        self.finish_btn = QPushButton("▶ 确认生成")
        self.finish_btn.setProperty("variant", "primary")
        self.finish_btn.clicked.connect(self.start_generation)
        self.cancel_task_btn = QPushButton("取消")
        self.cancel_task_btn.setProperty("variant", "danger")
        self.cancel_task_btn.clicked.connect(self.cancel_active_task)
        self.cancel_task_btn.setVisible(False)

        self.task_progress_label = QLabel("准备生成")
        self.task_progress_label.setObjectName("TaskProgressLabel")
        self.task_progress_label.setVisible(False)
        self.task_progress = QProgressBar()
        self.task_progress.setObjectName("TaskProgress")
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        self.task_progress.setTextVisible(False)
        self.task_progress.setFixedWidth(220)
        self.task_progress.setVisible(False)

        bottom.addWidget(self.task_progress_label)
        bottom.addWidget(self.task_progress)
        bottom.addStretch(1)
        bottom.addWidget(self.prev_btn)
        bottom.addWidget(self.next_btn)
        bottom.addWidget(self.preview_btn)
        bottom.addWidget(self.finish_btn)
        bottom.addWidget(self.cancel_task_btn)
        main.addWidget(self.action_bar)

        for surface in (self.sidebar,):
            self.apply_soft_shadow(surface, blur_radius=24, y_offset=6)

    def make_scroll_page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("ScrollViewport")
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("PageCanvas")
        content.setAutoFillBackground(False)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 0, 12, 4)
        layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("PageSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        scroll.setWidget(content)
        return scroll, layout

    def create_card(self, title: str) -> tuple[QGroupBox, QVBoxLayout]:
        card = QGroupBox(title)
        layout = QVBoxLayout(card)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 22, 18, 18)
        return card, layout

    def create_step1(self) -> QWidget:
        page, layout = self.make_scroll_page("1. 模型配置", "设置公司名称、云端模型或本地模型。云端模式支持 OpenAI 兼容接口，本地模式支持 Ollama / LM Studio / OpenAI 兼容服务。")

        company_card, company_layout = self.create_card("公司基础信息")
        grid = QGridLayout()
        self.company_name_edit = QLineEdit()
        self.company_name_edit.setPlaceholderText("例如：华为、特斯拉、某某科技")
        self.company_slug_edit = QLineEdit()
        self.company_slug_edit.setPlaceholderText("自动生成，例如 huawei")
        self.company_name_edit.textChanged.connect(self.on_company_name_change)
        grid.addWidget(QLabel("公司名称"), 0, 0)
        grid.addWidget(self.company_name_edit, 0, 1)
        grid.addWidget(QLabel("公司标识"), 1, 0)
        grid.addWidget(self.company_slug_edit, 1, 1)
        company_layout.addLayout(grid)
        layout.addWidget(company_card)

        template_card, template_layout = self.create_card("模板库")
        template_hint = QLabel("选择或维护组织模板，快速预填部门结构和生成偏好；不会覆盖模型配置、API Key、已上传文件或已添加成员。")
        template_hint.setObjectName("SmallHint")
        template_hint.setWordWrap(True)
        template_layout.addWidget(template_hint)

        template_row = QHBoxLayout()
        self.template_combo = QComboBox()
        self.template_combo.currentTextChanged.connect(self.update_template_preview)
        self.apply_template_btn = QPushButton("应用模板")
        self.apply_template_btn.setProperty("variant", "accent")
        self.apply_template_btn.clicked.connect(self.apply_selected_template)
        template_row.addWidget(QLabel("项目模板"))
        template_row.addWidget(self.template_combo, 1)
        template_row.addWidget(self.apply_template_btn)
        template_layout.addLayout(template_row)

        template_action_row = QHBoxLayout()
        self.new_template_btn = QPushButton("新建")
        self.new_template_btn.clicked.connect(self.create_custom_template)
        self.edit_template_btn = QPushButton("编辑")
        self.edit_template_btn.clicked.connect(self.edit_selected_template)
        self.copy_template_btn = QPushButton("复制")
        self.copy_template_btn.clicked.connect(self.copy_selected_template)
        self.delete_template_btn = QPushButton("删除")
        self.delete_template_btn.setProperty("variant", "danger")
        self.delete_template_btn.clicked.connect(self.delete_selected_template)
        self.import_template_btn = QPushButton("导入")
        self.import_template_btn.clicked.connect(self.import_template_dialog)
        self.export_template_btn = QPushButton("导出")
        self.export_template_btn.clicked.connect(self.export_selected_template)
        template_action_row.addStretch(1)
        for btn in (self.new_template_btn, self.edit_template_btn, self.copy_template_btn, self.delete_template_btn, self.import_template_btn, self.export_template_btn):
            template_action_row.addWidget(btn)
        template_layout.addLayout(template_action_row)

        self.template_preview = QTextEdit()
        self.template_preview.setReadOnly(True)
        self.template_preview.setMinimumHeight(120)
        self.template_preview.setObjectName("TemplatePreview")
        template_layout.addWidget(self.template_preview)
        layout.addWidget(template_card)

        model_card, model_layout = self.create_card("模型调用方式")
        mode_row = QHBoxLayout()
        self.cloud_radio = QRadioButton("云端模型")
        self.local_radio = QRadioButton("本地模型")
        self.cloud_radio.setChecked(True)
        self.model_type_group = QButtonGroup(self)
        self.model_type_group.addButton(self.cloud_radio)
        self.model_type_group.addButton(self.local_radio)
        self.cloud_radio.toggled.connect(self.on_model_type_change)
        mode_row.addWidget(self.cloud_radio)
        mode_row.addWidget(self.local_radio)
        mode_row.addStretch(1)
        model_layout.addLayout(mode_row)

        self.cloud_panel = QFrame()
        cloud_grid = QGridLayout(self.cloud_panel)
        self.provider_combo = QComboBox()
        self.provider_combo.setEditable(True)
        self.provider_combo.addItems(["deepseek", "openai", "moonshot", "其他"])
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("云端接口 API Key")
        self.api_base_edit = QLineEdit("https://api.deepseek.com")
        self.model_edit = QLineEdit("deepseek-chat")
        for widget in (self.provider_combo,):
            widget.currentTextChanged.connect(self.invalidate_model_test_state)
        for widget in (self.api_key_edit, self.api_base_edit, self.model_edit):
            widget.textChanged.connect(self.invalidate_model_test_state)
        self.show_key_check = QCheckBox("显示 API Key")
        self.show_key_check.toggled.connect(lambda checked: self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        cloud_grid.addWidget(QLabel("服务商"), 0, 0)
        cloud_grid.addWidget(self.provider_combo, 0, 1)
        cloud_grid.addWidget(QLabel("API Key"), 1, 0)
        cloud_grid.addWidget(self.api_key_edit, 1, 1)
        cloud_grid.addWidget(self.show_key_check, 1, 2)
        cloud_grid.addWidget(QLabel("API Base"), 2, 0)
        cloud_grid.addWidget(self.api_base_edit, 2, 1, 1, 2)
        cloud_grid.addWidget(QLabel("模型名称"), 3, 0)
        cloud_grid.addWidget(self.model_edit, 3, 1, 1, 2)
        model_layout.addWidget(self.cloud_panel)

        self.local_panel = QFrame()
        local_grid = QGridLayout(self.local_panel)
        self.local_type_combo = QComboBox()
        self.local_type_combo.setEditable(True)
        self.local_type_combo.addItems(["ollama", "lm-studio", "其他"])
        self.local_model_combo = QComboBox()
        self.local_model_combo.setEditable(True)
        self.local_model_combo.addItem("qwen2.5:7b")
        self.local_host_edit = QLineEdit("http://localhost:11434")
        self.local_type_combo.currentTextChanged.connect(self.invalidate_model_test_state)
        self.local_model_combo.currentTextChanged.connect(self.invalidate_model_test_state)
        self.local_host_edit.textChanged.connect(self.invalidate_model_test_state)
        self.temperature_edit = QLineEdit("0.7")
        self.local_detect_btn = QPushButton("自动识别本地模型")
        self.local_detect_btn.clicked.connect(self.auto_detect_local_models)
        self.local_detect_label = QLabel("尚未检测本地模型。")
        self.local_detect_label.setObjectName("SmallHint")
        self.local_detect_label.setWordWrap(True)
        local_grid.addWidget(QLabel("本地服务类型"), 0, 0)
        local_grid.addWidget(self.local_type_combo, 0, 1)
        local_grid.addWidget(QLabel("模型名称"), 1, 0)
        local_grid.addWidget(self.local_model_combo, 1, 1)
        local_grid.addWidget(self.local_detect_btn, 1, 2)
        local_grid.addWidget(QLabel("服务地址"), 2, 0)
        local_grid.addWidget(self.local_host_edit, 2, 1, 1, 2)
        local_grid.addWidget(QLabel("Temperature"), 3, 0)
        local_grid.addWidget(self.temperature_edit, 3, 1)
        local_grid.addWidget(self.local_detect_label, 4, 0, 1, 3)
        model_layout.addWidget(self.local_panel)

        model_test_row = QHBoxLayout()
        self.model_test_btn = QPushButton("检查模型连接")
        self.model_test_btn.setProperty("variant", "accent")
        self.model_test_btn.clicked.connect(self.on_model_test_button_clicked)
        self.model_test_label = QLabel("建议在正式生成前检查模型连接。")
        self.model_test_label.setObjectName("SmallHint")
        self.model_test_label.setWordWrap(True)
        model_test_row.addWidget(self.model_test_btn)
        model_test_row.addWidget(self.model_test_label, 1)
        model_layout.addLayout(model_test_row)

        health_row = QHBoxLayout()
        self.health_check_btn = QPushButton("体检配置")
        self.health_check_btn.setProperty("variant", "accent")
        self.health_check_btn.clicked.connect(self.run_config_health_check_ui)
        self.health_status_label = QLabel("正式生成前可先做一次本地配置体检。")
        self.health_status_label.setObjectName("SmallHint")
        self.health_status_label.setWordWrap(True)
        health_row.addWidget(self.health_check_btn)
        health_row.addWidget(self.health_status_label, 1)
        model_layout.addLayout(health_row)

        self.health_report_text = QTextEdit()
        self.health_report_text.setReadOnly(True)
        self.health_report_text.setMinimumHeight(170)
        self.health_report_text.setPlaceholderText("点击“体检配置”后，这里会显示公司信息、模型配置、文件路径、Prompt 目录和运行目录检查结果。")
        model_layout.addWidget(self.health_report_text)

        self.save_config_check = QCheckBox("保存配置到 config.json（API Key 会脱敏）")
        self.save_config_check.setChecked(True)
        model_layout.addWidget(self.save_config_check)
        layout.addWidget(model_card)
        self.reload_template_combo()
        layout.addStretch(1)
        return page

    def create_step2(self) -> QWidget:
        page, layout = self.make_scroll_page("2. 资料上传", "上传公司资料、组织架构图、业务说明等。系统会自动识别疑似组织架构资料，后续部门配置只作为补充校准。")
        card, card_layout = self.create_card("公司资料文件")
        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加资料文件")
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self.add_company_files)
        remove_btn = QPushButton("移除选中文件")
        remove_btn.clicked.connect(self.remove_company_file)
        clear_btn = QPushButton("清空列表")
        clear_btn.clicked.connect(self.clear_company_files)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        card_layout.addLayout(btn_row)
        self.files_list = QListWidget()
        self.files_list.setMinimumHeight(300)
        card_layout.addWidget(self.files_list)
        self.files_hint_label = QLabel("当前未上传任何资料。")
        self.files_hint_label.setObjectName("SmallHint")
        card_layout.addWidget(self.files_hint_label)
        self.org_chart_hint_label = QLabel("未检测到组织架构图。")
        self.org_chart_hint_label.setObjectName("SmallHint")
        card_layout.addWidget(self.org_chart_hint_label)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def create_step3(self) -> QWidget:
        page, layout = self.make_scroll_page("3. 组织架构", "选择标准部门，并添加自定义部门。若上传了组织架构图，这里主要用于补充和校准。")
        card, card_layout = self.create_card("部门配置")
        self.org_status_label = QLabel("请选择需要纳入公司技能的标准部门；如已上传组织架构图，这里只作为补充校准。")
        self.org_status_label.setObjectName("SmallHint")
        self.org_status_label.setWordWrap(True)
        card_layout.addWidget(self.org_status_label)
        action_row = QHBoxLayout()
        select_all = QPushButton("全选标准部门")
        select_all.clicked.connect(self.select_all_departments)
        clear_all = QPushButton("清空标准部门")
        clear_all.clicked.connect(self.clear_all_departments)
        action_row.addWidget(select_all)
        action_row.addWidget(clear_all)
        action_row.addStretch(1)
        card_layout.addLayout(action_row)

        self.department_grid_widget = QWidget()
        self.department_grid = QGridLayout(self.department_grid_widget)
        self.department_vars: dict[str, QCheckBox] = {}
        for i, department in enumerate(DEFAULT_DEPARTMENTS):
            cb = QCheckBox(department)
            cb.setChecked(True)
            cb.toggled.connect(self.refresh_summary)
            self.department_vars[department] = cb
            self.department_grid.addWidget(cb, i // 3, i % 3)
        card_layout.addWidget(self.department_grid_widget)

        custom_group = QGroupBox("自定义部门")
        custom_layout = QVBoxLayout(custom_group)
        custom_row = QHBoxLayout()
        self.custom_department_edit = QLineEdit()
        self.custom_department_edit.setPlaceholderText("可一次输入多个部门，用中文逗号或英文逗号分隔")
        add_custom = QPushButton("新建部门")
        add_custom.clicked.connect(self.add_custom_department)
        custom_row.addWidget(self.custom_department_edit, 1)
        custom_row.addWidget(add_custom)
        custom_layout.addLayout(custom_row)
        self.custom_department_list = QListWidget()
        self.custom_department_list.setMinimumHeight(140)
        custom_layout.addWidget(self.custom_department_list)
        del_custom = QPushButton("删除选中部门")
        del_custom.clicked.connect(self.delete_custom_department)
        custom_layout.addWidget(del_custom, 0, Qt.AlignmentFlag.AlignRight)
        card_layout.addWidget(custom_group)

        notes_group = QGroupBox("组织补充说明")
        notes_layout = QVBoxLayout(notes_group)
        self.org_notes_edit = QLineEdit("参考大型公司主流部门设置，可按业务需要增删。")
        notes_layout.addWidget(self.org_notes_edit)
        self.org_notes_edit.textChanged.connect(self.refresh_summary)
        card_layout.addWidget(notes_group)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def create_step4(self) -> QWidget:
        page, layout = self.make_scroll_page("4. 组织人员蒸馏", "添加一个或多个组织成员。每个成员都可以绑定多份资料文件和多份 Skill / 蒸馏模型文件。")
        card, card_layout = self.create_card("成员编辑")
        self.personal_enabled_check = QCheckBox("启用组织成员插入与发展预测")
        self.personal_enabled_check.toggled.connect(self.update_personal_section_state)
        card_layout.addWidget(self.personal_enabled_check)

        grid = QGridLayout()
        self.member_name_edit = QLineEdit()
        self.member_name_edit.setPlaceholderText("例如：马斯克、张三、资深产品经理")
        self.member_type_combo = QComboBox()
        self.member_type_combo.addItems(["组织成员", "老板位成员", "外部Skill成员", "顾问", "员工"])
        self.member_authority_combo = QComboBox()
        self.member_authority_combo.addItems(["advisory", "collaborative", "owner", "decision", "decision-support"])
        self.personal_file_edit = QLineEdit()
        self.personal_file_edit.setPlaceholderText("可选择多个资料文件")
        self.personnel_model_file_edit = QLineEdit()
        self.personnel_model_file_edit.setPlaceholderText("可选择多个员工 Skill / 模型文件")
        self.personal_file_paths: list[str] = []
        self.personnel_model_file_paths: list[str] = []
        personal_file_btn = QPushButton("选择资料文件")
        personal_file_btn.clicked.connect(self.select_personal_file)
        self.import_members_dir_btn = QPushButton("批量导入成员目录")
        self.import_members_dir_btn.setToolTip("选择一个根目录；每个一级子文件夹会被当成一个组织成员，文件夹内可放多份 .md/.txt/.docx/.pdf 等资料。")
        self.import_members_dir_btn.clicked.connect(self.import_members_from_directory)
        model_file_btn = QPushButton("选择 Skill 文件")
        model_file_btn.clicked.connect(self.select_personnel_model_file)
        self.personal_level_combo = QComboBox()
        self.personal_level_combo.addItems(ORG_LEVELS)
        self.personal_level_combo.setCurrentText(ORG_LEVELS[6])
        self.personal_department_combo = QComboBox()
        self.personal_department_combo.addItems(self.department_options())
        self.personal_department_combo.setCurrentText(DEFAULT_DEPARTMENTS[5])
        self.personal_department_combo.currentTextChanged.connect(self.on_manual_department_selected)
        self.personal_position_combo = QComboBox()
        self.personal_position_combo.setEditable(True)
        self.personal_position_combo.addItems(DEPARTMENT_ROLE_MAP.get(DEFAULT_DEPARTMENTS[5], []))
        self.detect_department_btn = QPushButton("自动识别归类")
        self.detect_department_btn.clicked.connect(self.auto_detect_employee_department)
        self.apply_detected_department_btn = QPushButton("采用识别结果")
        self.apply_detected_department_btn.clicked.connect(self.apply_detected_employee_department)
        self.mark_manual_department_btn = QPushButton("保存为人工标注")
        self.mark_manual_department_btn.clicked.connect(self.mark_manual_department_assignment)
        self.department_detection_status_label = QLabel("尚未识别员工资料所属部门。")
        self.department_detection_status_label.setObjectName("SmallHint")
        self.department_detection_reason_label = QLabel("")
        self.department_detection_reason_label.setObjectName("SmallHint")
        self.department_detection_reason_label.setWordWrap(True)
        self.personal_notes_edit = QTextEdit()
        self.personal_notes_edit.setMinimumHeight(110)
        self.has_peer_models_check = QCheckBox("已具备同组织/同层级其他人员蒸馏模型（勾选则不再联网补全岗位信息）")

        grid.addWidget(QLabel("成员名称"), 0, 0)
        grid.addWidget(self.member_name_edit, 0, 1)
        grid.addWidget(QLabel("类型"), 0, 2)
        grid.addWidget(self.member_type_combo, 0, 3)
        grid.addWidget(QLabel("权限"), 0, 4)
        grid.addWidget(self.member_authority_combo, 0, 5)
        grid.addWidget(QLabel("人员资料"), 1, 0)
        grid.addWidget(self.personal_file_edit, 1, 1, 1, 3)
        grid.addWidget(personal_file_btn, 1, 4)
        grid.addWidget(self.import_members_dir_btn, 1, 5)
        grid.addWidget(QLabel("Skill / 模型"), 2, 0)
        grid.addWidget(self.personnel_model_file_edit, 2, 1, 1, 4)
        grid.addWidget(model_file_btn, 2, 5)
        grid.addWidget(QLabel("插入层级"), 3, 0)
        grid.addWidget(self.personal_level_combo, 3, 1)
        grid.addWidget(QLabel("插入部门"), 3, 2)
        grid.addWidget(self.personal_department_combo, 3, 3)
        grid.addWidget(QLabel("岗位/位置"), 3, 4)
        grid.addWidget(self.personal_position_combo, 3, 5)
        grid.addWidget(self.detect_department_btn, 4, 1)
        grid.addWidget(self.apply_detected_department_btn, 4, 2)
        grid.addWidget(self.mark_manual_department_btn, 4, 3)
        grid.addWidget(self.department_detection_status_label, 5, 1, 1, 5)
        grid.addWidget(self.department_detection_reason_label, 6, 1, 1, 5)
        grid.addWidget(QLabel("补充说明"), 7, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.personal_notes_edit, 7, 1, 1, 5)
        grid.addWidget(self.has_peer_models_check, 8, 1, 1, 5)
        card_layout.addLayout(grid)

        btn_row = QHBoxLayout()
        self.add_member_btn = QPushButton("添加/更新当前成员")
        self.add_member_btn.setProperty("variant", "primary")
        self.add_member_btn.clicked.connect(self.add_or_update_organization_member)
        self.new_member_btn = QPushButton("清空表单新建")
        self.new_member_btn.clicked.connect(self.clear_member_form)
        self.delete_member_btn = QPushButton("删除选中成员")
        self.delete_member_btn.setProperty("variant", "danger")
        self.delete_member_btn.clicked.connect(self.delete_organization_member)
        btn_row.addStretch(1)
        btn_row.addWidget(self.add_member_btn)
        btn_row.addWidget(self.new_member_btn)
        btn_row.addWidget(self.delete_member_btn)
        card_layout.addLayout(btn_row)

        member_group = QGroupBox("已插入组织成员")
        member_layout = QVBoxLayout(member_group)
        self.organization_members_list = QListWidget()
        self.organization_members_list.setMinimumHeight(180)
        self.organization_members_list.currentRowChanged.connect(self.on_organization_member_selected)
        member_layout.addWidget(self.organization_members_list)
        card_layout.addWidget(member_group)

        layout.addWidget(card)
        layout.addStretch(1)
        self.update_personal_section_state()
        return page

    def create_step5(self) -> QWidget:
        page, layout = self.make_scroll_page("5. 生成确认", "先预览当前配置，不调用模型；确认无误后再正式生成 Skill 文件。")
        split = QHBoxLayout()
        summary_group = QGroupBox("配置摘要 / 搜索建议")
        summary_layout = QVBoxLayout(summary_group)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(False)
        summary_layout.addWidget(self.summary_text)
        notes_group = QGroupBox("最终补充说明")
        notes_layout = QVBoxLayout(notes_group)
        self.confirmation_notes_edit = QTextEdit()
        self.confirmation_notes_edit.setPlaceholderText("可补充重点部门、能力偏好、发言人风格、职业预测关注方向等。")
        self.confirmation_notes_edit.textChanged.connect(self.refresh_summary)
        refresh_btn = QPushButton("刷新配置摘要")
        refresh_btn.clicked.connect(self.refresh_summary)
        notes_layout.addWidget(self.confirmation_notes_edit)
        notes_layout.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignRight)
        split.addWidget(summary_group, 1)
        split.addWidget(notes_group, 1)
        layout.addLayout(split, 1)

        self.result_group = QGroupBox("生成结果")
        result_layout = QVBoxLayout(self.result_group)
        self.output_dir_label = QLabel("尚未生成输出文件。")
        self.output_dir_label.setObjectName("SmallHint")
        self.output_dir_label.setWordWrap(True)
        result_layout.addWidget(self.output_dir_label)

        self.quality_summary_label = QLabel("生成完成后将自动执行质量检查。")
        self.quality_summary_label.setObjectName("SmallHint")
        self.quality_summary_label.setWordWrap(True)
        result_layout.addWidget(self.quality_summary_label)

        self.clean_output_check = QCheckBox("生成前清理同名旧产物")
        self.clean_output_check.setChecked(True)
        self.clean_output_check.setToolTip("正式生成前删除同名 companies / agent_exports / preview_reports 旧结果，避免旧文件干扰检查。")
        self.clean_output_check.stateChanged.connect(self.refresh_summary)
        result_layout.addWidget(self.clean_output_check)

        self.quality_report = QTextEdit()
        self.quality_report.setReadOnly(True)
        self.quality_report.setMinimumHeight(110)
        self.quality_report.setPlaceholderText("生成完成后，这里会显示必要文件、空文件、推理标签残留、失败话术等检查结果。")
        result_layout.addWidget(self.quality_report)

        result_split = QHBoxLayout()
        self.result_files_list = QListWidget()
        self.result_files_list.setMinimumHeight(150)
        self.result_files_list.currentItemChanged.connect(self.on_result_file_selected)
        self.result_preview = QTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setPlaceholderText("生成完成后，可在这里预览输出文件内容。")
        result_split.addWidget(self.result_files_list, 1)
        result_split.addWidget(self.result_preview, 2)
        result_layout.addLayout(result_split)

        result_buttons = QHBoxLayout()
        self.open_output_dir_btn = QPushButton("打开输出目录")
        self.open_output_dir_btn.clicked.connect(self.open_output_directory)
        self.copy_output_path_btn = QPushButton("复制输出路径")
        self.copy_output_path_btn.clicked.connect(self.copy_output_path)
        self.open_selected_file_btn = QPushButton("打开选中文件")
        self.open_selected_file_btn.clicked.connect(self.open_selected_result_file)
        self.refresh_quality_btn = QPushButton("重新检查质量")
        self.refresh_quality_btn.clicked.connect(self.refresh_output_quality)
        result_buttons.addStretch(1)
        result_buttons.addWidget(self.refresh_quality_btn)
        result_buttons.addWidget(self.open_output_dir_btn)
        result_buttons.addWidget(self.copy_output_path_btn)
        result_buttons.addWidget(self.open_selected_file_btn)
        result_layout.addLayout(result_buttons)
        self.result_group.setVisible(False)
        layout.addWidget(self.result_group)

        self.history_group = QGroupBox("生成历史")
        history_layout = QVBoxLayout(self.history_group)
        self.history_hint_label = QLabel("每次正式生成完成后会自动记录历史，便于回溯输出目录、日志和质量结果。")
        self.history_hint_label.setObjectName("SmallHint")
        self.history_hint_label.setWordWrap(True)
        history_layout.addWidget(self.history_hint_label)

        history_split = QHBoxLayout()
        self.generation_history_list = QListWidget()
        self.generation_history_list.setMinimumHeight(150)
        self.generation_history_list.currentItemChanged.connect(self.on_generation_history_selected)
        self.generation_history_detail = QTextEdit()
        self.generation_history_detail.setReadOnly(True)
        self.generation_history_detail.setPlaceholderText("选择一条历史记录后，这里会显示公司、模型、输出目录、日志文件和质量状态。")
        history_split.addWidget(self.generation_history_list, 1)
        history_split.addWidget(self.generation_history_detail, 2)
        history_layout.addLayout(history_split)

        history_buttons = QHBoxLayout()
        self.refresh_history_btn = QPushButton("刷新历史")
        self.refresh_history_btn.clicked.connect(self.refresh_generation_history)
        self.open_history_output_btn = QPushButton("打开历史输出")
        self.open_history_output_btn.clicked.connect(self.open_history_output_directory)
        self.open_history_log_btn = QPushButton("打开历史日志")
        self.open_history_log_btn.clicked.connect(self.open_history_log_file)
        self.copy_history_output_btn = QPushButton("复制历史路径")
        self.copy_history_output_btn.clicked.connect(self.copy_history_output_path)
        history_buttons.addStretch(1)
        history_buttons.addWidget(self.refresh_history_btn)
        history_buttons.addWidget(self.open_history_output_btn)
        history_buttons.addWidget(self.open_history_log_btn)
        history_buttons.addWidget(self.copy_history_output_btn)
        history_layout.addLayout(history_buttons)
        layout.addWidget(self.history_group)
        return page

    # ---------- template library ----------
    def reload_template_combo(self, preferred_name: str | None = None):
        if not hasattr(self, "template_combo"):
            return
        current_name = preferred_name or self.template_combo.currentText()
        templates = all_templates()
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItems([template.name for template in templates])
        self.template_combo.blockSignals(False)
        if current_name:
            idx = self.template_combo.findText(current_name)
            if idx >= 0:
                self.template_combo.setCurrentIndex(idx)
        self.update_template_preview()

    def current_template(self):
        if not hasattr(self, "template_combo"):
            return None
        return get_template_by_name(self.template_combo.currentText())

    def update_template_preview(self):
        if not hasattr(self, "template_preview") or not hasattr(self, "template_combo"):
            return
        template = self.current_template()
        self.template_preview.setPlainText(format_template_preview(template))
        if hasattr(self, "delete_template_btn"):
            self.delete_template_btn.setEnabled(is_custom_template(template))
        if hasattr(self, "export_template_btn"):
            self.export_template_btn.setEnabled(template is not None)
        if hasattr(self, "edit_template_btn"):
            self.edit_template_btn.setEnabled(template is not None)
        if hasattr(self, "copy_template_btn"):
            self.copy_template_btn.setEnabled(template is not None)

    def create_custom_template(self):
        ok, data = TemplateEditorDialog.edit(self, {}, "新建自定义模板")
        if not ok:
            return
        if not data.get("name"):
            self.show_error_dialog("模板名称不能为空", "请先填写模板名称。")
            return
        try:
            template = upsert_custom_template(data)
            self.reload_template_combo(template.name)
            self.log_message(f"🧩 已新建模板：{template.name}")
            self.show_info_dialog("模板已保存", f"已创建自定义模板：{template.name}")
        except Exception as exc:
            self.show_error_dialog("保存模板失败", str(exc))

    def edit_selected_template(self):
        template = self.current_template()
        if template is None:
            self.show_error_dialog("模板不可用", "未找到当前选择的模板。")
            return
        data = template_to_edit_data(template)
        original_name = template.name if is_custom_template(template) else None
        if not is_custom_template(template):
            data["name"] = unique_template_name(f"{template.name} 自定义")
        ok, edited = TemplateEditorDialog.edit(self, data, "编辑模板")
        if not ok:
            return
        if not edited.get("name"):
            self.show_error_dialog("模板名称不能为空", "请先填写模板名称。")
            return
        try:
            saved = upsert_custom_template(edited, original_name=original_name)
            self.reload_template_combo(saved.name)
            self.log_message(f"🧩 已保存模板：{saved.name}")
            self.show_info_dialog("模板已保存", f"已保存自定义模板：{saved.name}")
        except Exception as exc:
            self.show_error_dialog("保存模板失败", str(exc))

    def copy_selected_template(self):
        template = self.current_template()
        if template is None:
            self.show_error_dialog("模板不可用", "未找到当前选择的模板。")
            return
        data = template_to_edit_data(template)
        data["name"] = unique_template_name(f"{template.name} 副本")
        try:
            saved = upsert_custom_template(data)
            self.reload_template_combo(saved.name)
            self.log_message(f"🧩 已复制模板：{saved.name}")
            self.show_info_dialog("模板已复制", f"已创建副本：{saved.name}")
        except Exception as exc:
            self.show_error_dialog("复制模板失败", str(exc))

    def delete_selected_template(self):
        template = self.current_template()
        if template is None:
            self.show_error_dialog("模板不可用", "未找到当前选择的模板。")
            return
        if not is_custom_template(template):
            self.show_error_dialog("不能删除内置模板", "内置模板用于初始化和升级保护。如需修改，请先复制或编辑为自定义模板。")
            return
        if not self.show_confirm_dialog("删除模板", f"确定删除自定义模板：{template.name}？", confirm_text="删除", cancel_text="取消"):
            return
        try:
            if delete_custom_template(template.name):
                self.reload_template_combo()
                self.log_message(f"🗑 已删除模板：{template.name}")
                self.show_info_dialog("模板已删除", f"已删除：{template.name}")
            else:
                self.show_error_dialog("删除失败", "未找到可删除的自定义模板。")
        except Exception as exc:
            self.show_error_dialog("删除模板失败", str(exc))

    def import_template_dialog(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "导入模板",
            str(Path.cwd()),
            "算疏智合模板 (*.json);;JSON 文件 (*.json);;所有文件 (*)",
        )
        if not filename:
            return
        try:
            template = import_template(Path(filename))
            self.reload_template_combo(template.name)
            self.log_message(f"⬇️ 已导入模板：{template.name}")
            self.show_info_dialog("模板已导入", f"已导入为自定义模板：{template.name}")
        except Exception as exc:
            self.show_error_dialog("导入模板失败", str(exc))

    def export_selected_template(self):
        template = self.current_template()
        if template is None:
            self.show_error_dialog("模板不可用", "未找到当前选择的模板。")
            return
        safe_name = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", template.name).strip("-") or "template"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出模板",
            str(Path.cwd() / f"{safe_name}.json"),
            "算疏智合模板 (*.json);;JSON 文件 (*.json)",
        )
        if not filename:
            return
        try:
            export_template(template, Path(filename))
            self.log_message(f"⬆️ 已导出模板：{filename}")
            self.show_info_dialog("模板已导出", f"模板已导出到：\n{filename}")
        except Exception as exc:
            self.show_error_dialog("导出模板失败", str(exc))

    def apply_selected_template(self):
        if not hasattr(self, "template_combo"):
            return
        template = self.current_template()
        if template is None:
            self.show_error_dialog("模板不可用", "未找到当前选择的模板。")
            return
        message = (
            f"将应用模板：{template.name}\n\n"
            "会更新：标准部门、自定义部门、组织补充说明、最终补充说明。\n"
            "不会覆盖：公司名称、模型配置、API Key、上传文件、已添加组织成员。"
        )
        if not self.show_confirm_dialog("应用模板", message, confirm_text="应用", cancel_text="取消"):
            return
        selected = set(template.selected_departments)
        for department, cb in self.department_vars.items():
            cb.setChecked(department in selected)
        for department in template.custom_departments:
            if department and department not in self.custom_departments_list and department not in DEFAULT_DEPARTMENTS:
                self.custom_departments_list.append(department)
        self.rebuild_custom_departments_list()
        self.org_notes_edit.setText(template.org_notes)
        current_notes = self.confirmation_notes_edit.toPlainText().strip()
        if not current_notes:
            self.confirmation_notes_edit.setPlainText(template.confirmation_notes)
        elif template.confirmation_notes and template.confirmation_notes not in current_notes:
            self.confirmation_notes_edit.setPlainText(current_notes + "\n\n" + template.confirmation_notes)
        self.update_department_options()
        self.refresh_summary()
        self.log_message(f"✅ 已应用模板：{template.name}")
        self.show_info_dialog("模板已应用", f"已应用：{template.name}")

    # ---------- state helpers ----------
    def model_type(self) -> str:
        return "cloud" if self.cloud_radio.isChecked() else "local"

    def on_company_name_change(self):
        if not self.company_slug_edit.text().strip() or is_invalid_company_slug(self.company_slug_edit.text()):
            self.company_slug_edit.setText(normalize_company_slug(self.company_slug_edit.text(), self.company_name_edit.text()))

    def invalidate_model_test_state(self, *_args):
        self.last_model_test_ok = None
        self.last_model_test_message = ""
        if hasattr(self, "model_test_label"):
            self.model_test_label.setText("模型配置已变更，建议重新检查连接。")

    def on_model_type_change(self):
        is_cloud = self.cloud_radio.isChecked()
        self.cloud_panel.setVisible(is_cloud)
        self.local_panel.setVisible(not is_cloud)

    def selected_departments(self) -> list[str]:
        return [department for department, cb in self.department_vars.items() if cb.isChecked()]

    def custom_departments(self) -> list[str]:
        return self.custom_departments_list[:]

    def department_options(self) -> list[str]:
        options: list[str] = []
        for item in DEFAULT_DEPARTMENTS + self.custom_departments_list:
            if item and item not in options:
                options.append(item)
        return options

    def department_classification_candidates(self) -> list[str]:
        return self.department_options()

    def entry_paths(self, entry_value: str, stored_paths: list[str] | None = None) -> list[str]:
        return merge_file_path_values(stored_paths or [], entry_value)

    def profile_file_paths(self) -> list[str]:
        return self.entry_paths(self.personal_file_edit.text().strip(), self.personal_file_paths)

    def model_file_paths(self) -> list[str]:
        return self.entry_paths(self.personnel_model_file_edit.text().strip(), self.personnel_model_file_paths)

    def set_profile_file_paths(self, paths: object):
        self.personal_file_paths = merge_file_path_values(paths)
        self.personal_file_edit.setText("; ".join(self.personal_file_paths))

    def set_model_file_paths(self, paths: object):
        self.personnel_model_file_paths = merge_file_path_values(paths)
        self.personnel_model_file_edit.setText("; ".join(self.personnel_model_file_paths))

    def file_count_label(self, paths: object) -> str:
        normalized = merge_file_path_values(paths)
        if not normalized:
            return "未上传"
        if len(normalized) == 1:
            return Path(normalized[0]).name
        preview = "，".join(Path(path).name for path in normalized[:3])
        return f"{len(normalized)} 个文件：{preview}" + (" 等" if len(normalized) > 3 else "")

    def refresh_org_detection(self):
        self.org_chart_files = [path for path in self.company_files if looks_like_org_chart_file(path)]
        detected = bool(self.org_chart_files)
        if detected and self.org_notes_edit.text().strip() == "参考大型公司主流部门设置，可按业务需要增删。":
            self.org_notes_edit.setText("已上传组织架构图/组织架构资料，生成时优先参考上传资料；标准部门勾选仅作为补充。")
        if detected:
            names = "，".join(Path(path).name for path in self.org_chart_files[:3])
            self.org_chart_hint_label.setText(f"✅ 已识别组织架构资料：{names}。下一步可直接继续，部门勾选仅作补充。")
            self.org_status_label.setText(f"已上传组织架构资料：{names}。本页部门勾选不是必填，只用于对识别结果做补充校准。")
        else:
            self.org_chart_hint_label.setText("未检测到组织架构图。")
            self.org_status_label.setText("请选择需要纳入公司技能的标准部门；如已上传组织架构图，这里只作为补充校准。")

    def update_files_hint(self):
        if self.company_files:
            self.files_hint_label.setText(f"已上传 {len(self.company_files)} 个文件，生成时将自动抽取摘要。")
        else:
            self.files_hint_label.setText("当前未上传任何资料。")

    def update_department_options(self):
        current = self.personal_department_combo.currentText() if hasattr(self, "personal_department_combo") else ""
        self.personal_department_combo.blockSignals(True)
        self.personal_department_combo.clear()
        self.personal_department_combo.addItems(self.department_options())
        if current in self.department_options():
            self.personal_department_combo.setCurrentText(current)
        else:
            self.personal_department_combo.setCurrentText(DEFAULT_DEPARTMENTS[5] if DEFAULT_DEPARTMENTS[5] in self.department_options() else (self.department_options()[0] if self.department_options() else ""))
        self.personal_department_combo.blockSignals(False)
        self.on_personal_department_change()

    def on_personal_department_change(self):
        department = self.personal_department_combo.currentText()
        roles = DEPARTMENT_ROLE_MAP.get(department, ["岗位经理", "高级专员", "核心成员"])
        current = self.personal_position_combo.currentText()
        self.personal_position_combo.clear()
        self.personal_position_combo.addItems(roles)
        if current:
            self.personal_position_combo.setCurrentText(current if current in roles else roles[0])
        elif roles:
            self.personal_position_combo.setCurrentText(roles[0])

    def on_manual_department_selected(self):
        if not self._applying_auto_department:
            self.mark_manual_department_assignment(refresh_only=True)
        self.on_personal_department_change()
        self.refresh_summary()

    # ---------- file and list operations ----------
    def add_company_files(self):
        filenames, _ = QFileDialog.getOpenFileNames(self, "选择公司资料文件", os.getcwd(), file_filter("支持的资料文件", SUPPORTED_COMPANY_FILE_TYPES))
        if not filenames:
            return
        for filename in filenames:
            if filename not in self.company_files:
                self.company_files.append(filename)
                self.files_list.addItem(filename)
                self.log_message(f"✅ 已添加资料文件：{filename}")
        self.refresh_org_detection()
        self.update_files_hint()
        self.refresh_summary()

    def remove_company_file(self):
        row = self.files_list.currentRow()
        if row < 0:
            return
        removed = self.company_files.pop(row)
        self.files_list.takeItem(row)
        self.log_message(f"🗑 已移除资料文件：{removed}")
        self.refresh_org_detection()
        self.update_files_hint()
        self.refresh_summary()

    def clear_company_files(self):
        self.company_files.clear()
        self.files_list.clear()
        self.refresh_org_detection()
        self.update_files_hint()
        self.refresh_summary()

    def select_all_departments(self):
        for cb in self.department_vars.values():
            cb.setChecked(True)
        self.refresh_summary()

    def clear_all_departments(self):
        for cb in self.department_vars.values():
            cb.setChecked(False)
        self.refresh_summary()

    def add_custom_department(self):
        raw = self.custom_department_edit.text().strip()
        if not raw:
            self.show_info_dialog("提示", "请先输入要新建的部门名称。")
            return
        items = [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
        added = []
        for item in items:
            if item not in self.custom_departments_list and item not in DEFAULT_DEPARTMENTS:
                self.custom_departments_list.append(item)
                self.custom_department_list.addItem(item)
                added.append(item)
        self.custom_department_edit.clear()
        self.update_department_options()
        self.refresh_summary()
        if added:
            self.log_message(f"✅ 已新建自定义部门：{'，'.join(added)}")

    def delete_custom_department(self):
        row = self.custom_department_list.currentRow()
        if row < 0:
            return
        removed = self.custom_departments_list.pop(row)
        self.custom_department_list.takeItem(row)
        self.update_department_options()
        self.refresh_summary()
        self.log_message(f"🗑 已删除自定义部门：{removed}")

    def rebuild_custom_departments_list(self):
        self.custom_department_list.clear()
        for department in self.custom_departments_list:
            self.custom_department_list.addItem(department)
        self.update_department_options()

    def select_personal_file(self):
        filenames, _ = QFileDialog.getOpenFileNames(self, "选择组织人员资料文件（可多选）", os.getcwd(), file_filter("支持的文件", SUPPORTED_PERSONAL_FILE_TYPES))
        if filenames:
            self.set_profile_file_paths(filenames)
            self.log_message(f"✅ 已选择组织人员资料文件：{self.file_count_label(self.personal_file_paths)}")
            self.refresh_summary()
            if self.personal_enabled_check.isChecked():
                if len(filenames) <= AUTO_DETECT_PERSONNEL_FILE_LIMIT:
                    self.auto_detect_employee_department()
                else:
                    self.department_detection_status_label.setText(
                        f"已选择 {len(filenames)} 个员工资料文件。为避免多文件解析卡住，已跳过自动识别；可手动选择部门，或点击“自动识别归类”进行预览识别。"
                    )
                    self.department_detection_reason_label.setText(
                        "提示：这里的多选会被视为同一个组织成员的资料包；如果是多名员工，建议逐个成员添加。"
                    )
                    self.log_message("ℹ️ 多文件人员资料已选择，未自动触发部门识别。")

    def select_personnel_model_file(self):
        filenames, _ = QFileDialog.getOpenFileNames(self, "选择组织人员蒸馏模型文件（可多选）", os.getcwd(), file_filter("支持的模型/资料文件", SUPPORTED_PERSONNEL_MODEL_FILE_TYPES))
        if filenames:
            self.set_model_file_paths(filenames)
            self.log_message(f"✅ 已选择组织人员蒸馏模型文件：{self.file_count_label(self.personnel_model_file_paths)}")
            self.refresh_summary()

    def import_members_from_directory(self):
        root_dir = QFileDialog.getExistingDirectory(self, "选择批量成员根目录（每个子文件夹代表一个成员）", os.getcwd())
        if not root_dir:
            return
        if not self.personal_enabled_check.isChecked():
            self.personal_enabled_check.setChecked(True)
        self.import_members_dir_btn.setEnabled(False)
        self.department_detection_status_label.setText("正在扫描批量成员目录...")
        self.department_detection_reason_label.setText("目录结构要求：一个员工一个一级子文件夹；每个子文件夹内可放多份 md/txt/docx/pdf。")
        self.log_message(f"📁 开始批量导入成员目录：{root_dir}")
        thread = QThread(self)
        worker = BatchMemberImportWorker(
            root_dir,
            self.department_classification_candidates(),
            self.personal_level_combo.currentText().strip() or ORG_LEVELS[6],
            self.personal_department_combo.currentText().strip() or DEFAULT_DEPARTMENTS[5],
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.handle_batch_member_import_progress)
        worker.finished.connect(self.handle_batch_member_import_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda _result=None, _error_text='', w=worker: self._cleanup_worker(w))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread))
        self._threads.append(thread)
        self._workers.append(worker)
        thread.start()

    def handle_batch_member_import_progress(self, message: str):
        if message:
            self.department_detection_status_label.setText(message)
            self.log_message(f"📁 {message}")

    def handle_batch_member_import_finished(self, result: object, error_text: str):
        self.import_members_dir_btn.setEnabled(self.personal_enabled_check.isChecked())
        if error_text:
            self.department_detection_status_label.setText("批量成员目录导入失败。")
            self.department_detection_reason_label.setText(error_text)
            self.log_message(f"❌ 批量成员目录导入失败：{error_text}")
            self.show_error_dialog("批量导入失败", error_text)
            return
        data = result if isinstance(result, dict) else {}
        members = data.get("members") or []
        warnings = data.get("warnings") or []
        skipped = data.get("skipped") or []
        before = len(self.organization_members)
        for member in members:
            if isinstance(member, dict):
                self.organization_members.append(member)
        self.personal_enabled_check.setChecked(True)
        self.editing_member_index = None
        self.refresh_organization_members_list()
        imported = len(self.organization_members) - before
        self.department_detection_status_label.setText(f"已批量导入 {imported} 个组织成员。")
        detail_lines = []
        if warnings:
            detail_lines.append("提醒：" + "；".join(str(item) for item in warnings[:4]))
        if skipped:
            detail_lines.append("跳过：" + "；".join(str(item) for item in skipped[:6]))
        self.department_detection_reason_label.setText("\n".join(detail_lines))
        self.log_message(f"✅ 已从目录批量导入组织成员：{imported} 个")
        if skipped:
            self.log_message(f"⚠️ 批量导入跳过 {len(skipped)} 个目录。")
        message = f"已导入 {imported} 个组织成员。"
        if warnings or skipped:
            message += "\n\n" + "\n".join(detail_lines)
        self.show_info_dialog("批量导入完成", message)

    # ---------- organization member operations ----------
    def build_department_assignment_payload(self) -> dict:
        assignment = dict(self.employee_department_detection or {})
        mode = assignment.get("mode") or "manual"
        manual_department = self.personal_department_combo.currentText().strip()
        assignment.update({"mode": mode, "manual_department": manual_department, "final_department": manual_department})
        if not assignment.get("department"):
            assignment["department"] = manual_department
        return assignment

    def current_member_payload(self) -> dict:
        name = self.member_name_edit.text().strip()
        profile_paths = self.profile_file_paths()
        model_paths = self.model_file_paths()
        if not name:
            source = (model_paths or profile_paths or [""])[0]
            name = self.personal_position_combo.currentText().strip() or (Path(source).stem if source else f"组织成员{len(self.organization_members) + 1}")
        return {
            "enabled": True,
            "member_name": name,
            "member_type": self.member_type_combo.currentText().strip() or "组织成员",
            "level": self.personal_level_combo.currentText().strip(),
            "department": self.personal_department_combo.currentText().strip(),
            "position": self.personal_position_combo.currentText().strip(),
            "role_notes": self.personal_notes_edit.toPlainText().strip(),
            "has_peer_models": bool(self.has_peer_models_check.isChecked()),
            "profile_file_path": profile_paths[0] if profile_paths else "",
            "profile_file_paths": profile_paths,
            "skill_file_path": model_paths[0] if model_paths else "",
            "skill_file_paths": model_paths,
            "file_path": profile_paths[0] if profile_paths else "",
            "file_paths": profile_paths,
            "model_file_path": model_paths[0] if model_paths else "",
            "model_file_paths": model_paths,
            "department_assignment": self.build_department_assignment_payload(),
            "insert_mode": "append_non_destructive",
            "authority_level": self.member_authority_combo.currentText().strip() or "advisory",
        }

    def member_display_text(self, member: dict, index: int) -> str:
        name = member.get("member_name") or member.get("position") or f"组织成员{index}"
        level = member.get("level") or "未设层级"
        department = member.get("department") or member.get("department_assignment", {}).get("final_department") or "未设部门"
        position = member.get("position") or "未设岗位"
        profile_paths = merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path"))
        skill_paths = merge_file_path_values(member.get("skill_file_paths"), member.get("model_file_paths"), member.get("skill_file_path"), member.get("model_file_path"))
        source_parts = []
        if profile_paths:
            source_parts.append(f"资料{len(profile_paths)}")
        if skill_paths:
            source_parts.append(f"Skill{len(skill_paths)}")
        source = "+".join(source_parts) if source_parts else "无文件"
        return f"{index}. {name}｜{level}｜{department}｜{position}｜{source}｜非覆盖插入"

    def refresh_organization_members_list(self):
        self.organization_members_list.blockSignals(True)
        self.organization_members_list.clear()
        for idx, member in enumerate(self.organization_members, start=1):
            self.organization_members_list.addItem(self.member_display_text(member, idx))
        self.organization_members_list.blockSignals(False)
        self.refresh_summary()

    def add_or_update_organization_member(self):
        if not self.profile_file_paths() and not self.model_file_paths() and not self.personal_position_combo.currentText().strip():
            self.show_error_dialog("缺少成员信息", "请至少填写岗位名称，或选择员工资料/员工 Skill 文件。")
            return
        member = self.current_member_payload()
        if self.editing_member_index is not None and 0 <= self.editing_member_index < len(self.organization_members):
            self.organization_members[self.editing_member_index] = member
            self.log_message(f"✍️ 已更新组织成员：{member.get('member_name')}")
        else:
            self.organization_members.append(member)
            self.editing_member_index = len(self.organization_members) - 1
            self.log_message(f"➕ 已插入组织成员：{member.get('member_name')}")
        self.personal_enabled_check.setChecked(True)
        self.refresh_organization_members_list()

    def delete_organization_member(self):
        row = self.organization_members_list.currentRow()
        if row < 0:
            return
        if 0 <= row < len(self.organization_members):
            removed = self.organization_members.pop(row)
            self.log_message(f"🗑 已删除组织成员：{removed.get('member_name') or removed.get('position')}")
        self.editing_member_index = None
        self.refresh_organization_members_list()

    def clear_member_form(self):
        self.editing_member_index = None
        self.member_name_edit.clear()
        self.member_type_combo.setCurrentText("组织成员")
        self.member_authority_combo.setCurrentText("advisory")
        self.set_profile_file_paths([])
        self.set_model_file_paths([])
        self.personal_level_combo.setCurrentText(ORG_LEVELS[6])
        self.personal_department_combo.setCurrentText(DEFAULT_DEPARTMENTS[5])
        self.personal_position_combo.setCurrentText(DEPARTMENT_ROLE_MAP[DEFAULT_DEPARTMENTS[5]][0])
        self.personal_notes_edit.clear()
        self.employee_department_detection = {}
        self.department_detection_status_label.setText("尚未识别员工资料所属部门。")
        self.department_detection_reason_label.setText("")
        self.organization_members_list.clearSelection()
        self.refresh_summary()

    def on_organization_member_selected(self, row: int):
        if row < 0 or not (0 <= row < len(self.organization_members)):
            return
        member = self.organization_members[row]
        self.editing_member_index = row
        self.member_name_edit.setText(member.get("member_name", ""))
        self.member_type_combo.setCurrentText(member.get("member_type", "组织成员"))
        self.member_authority_combo.setCurrentText(member.get("authority_level", "advisory"))
        self.set_profile_file_paths(merge_file_path_values(member.get("profile_file_paths"), member.get("file_paths"), member.get("profile_file_path"), member.get("file_path")))
        self.set_model_file_paths(merge_file_path_values(member.get("skill_file_paths"), member.get("model_file_paths"), member.get("skill_file_path"), member.get("model_file_path")))
        self.personal_level_combo.setCurrentText(member.get("level") or ORG_LEVELS[6])
        self.personal_department_combo.setCurrentText(member.get("department") or DEFAULT_DEPARTMENTS[5])
        self.personal_position_combo.setCurrentText(member.get("position") or DEPARTMENT_ROLE_MAP.get(self.personal_department_combo.currentText(), ["核心成员"])[0])
        self.personal_notes_edit.setPlainText(member.get("role_notes", ""))
        self.has_peer_models_check.setChecked(bool(member.get("has_peer_models", True)))
        assignment = member.get("department_assignment", {}) or {}
        self.employee_department_detection = assignment
        self.department_detection_status_label.setText(f"当前编辑成员：{member.get('member_name') or member.get('position')}")
        self.department_detection_reason_label.setText(assignment.get("reason", ""))
        self.refresh_summary()

    # ---------- detection tasks ----------
    def auto_detect_employee_department(self):
        file_paths = self.profile_file_paths()
        if not file_paths:
            self.show_error_dialog("缺少文件", "请先选择至少一个组织人员资料文件。")
            return
        self.department_detection_run_id += 1
        run_id = self.department_detection_run_id
        self.detect_department_btn.setEnabled(False)
        if len(file_paths) > AUTO_DETECT_PERSONNEL_FILE_LIMIT:
            self.department_detection_status_label.setText(
                f"正在预览解析 {len(file_paths)} 个员工资料文件并识别所属部门，系统会自动截断大文件以避免卡住..."
            )
        else:
            self.department_detection_status_label.setText(f"正在本地解析 {len(file_paths)} 个员工资料文件并识别所属部门...")
        self.department_detection_reason_label.setText("")
        self.log_message(f"🔎 开始自动识别员工资料所属部门（{len(file_paths)} 个文件）...")
        thread = QThread(self)
        worker = DepartmentDetectWorker(file_paths, self.department_classification_candidates())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.handle_employee_department_detection_progress)
        worker.finished.connect(lambda result, error_text, rid=run_id: self.handle_employee_department_detection(result, error_text, rid))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda _result=None, _error_text='', w=worker: self._cleanup_worker(w))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread))
        self._threads.append(thread)
        self._workers.append(worker)
        QTimer.singleShot(DEPARTMENT_DETECTION_TIMEOUT_MS, lambda rid=run_id: self.handle_employee_department_detection_timeout(rid))
        thread.start()

    def handle_employee_department_detection_progress(self, message: str):
        if message:
            self.department_detection_status_label.setText(message)
            self.log_message(f"🔎 {message}")

    def handle_employee_department_detection_timeout(self, run_id: int):
        if run_id != self.department_detection_run_id:
            return
        if self.detect_department_btn.isEnabled():
            return
        self.department_detection_run_id += 1
        self.detect_department_btn.setEnabled(True)
        self.department_detection_status_label.setText("员工资料识别超时，已恢复操作。")
        self.department_detection_reason_label.setText(
            "通常是某个 PDF / docx 解析过慢或一次选择了过多人员资料。可先手动选择部门，或拆成单个成员资料后再识别。"
        )
        self.log_message("⏱️ 员工资料部门识别超时，已恢复按钮。")

    def handle_employee_department_detection(self, result: dict, error_text: str = "", run_id: int | None = None):
        if run_id is not None and run_id != self.department_detection_run_id:
            return
        self.detect_department_btn.setEnabled(True)
        if error_text:
            self.department_detection_status_label.setText(f"识别失败：{error_text}")
            self.department_detection_reason_label.setText("")
            self.log_message(f"❌ 员工资料部门识别失败：{error_text}")
            return
        self.employee_department_detection = result or {}
        department = (result or {}).get("detected_department") or (result or {}).get("department") or ""
        confidence = float((result or {}).get("confidence") or 0.0)
        reason = (result or {}).get("reason") or ""
        if department:
            self.department_detection_status_label.setText(f"识别结果：{department}，置信度 {confidence * 100:.1f}%")
            self.department_detection_reason_label.setText(f"依据：{reason}")
            self.log_message(f"✅ 员工资料所属部门识别：{department}（{confidence * 100:.1f}%）")
            if confidence >= 0.35:
                self.apply_detected_employee_department(silent=True)
        else:
            self.department_detection_status_label.setText("未能自动判断所属部门，请使用下拉框进行人工标注。")
            self.department_detection_reason_label.setText((result or {}).get("reason") or "未命中明显部门关键词。")
            self.log_message("⚠️ 未能自动判断员工资料所属部门，建议人工标注。")
        self.refresh_summary()

    def apply_detected_employee_department(self, silent: bool = False):
        department = self.employee_department_detection.get("detected_department") or self.employee_department_detection.get("department") or ""
        if not department:
            if not silent:
                self.show_info_dialog("无识别结果", "当前没有可采用的自动识别部门，请先点击“自动识别归类”。")
            return
        if department not in self.department_options():
            self.custom_departments_list.append(department)
            self.rebuild_custom_departments_list()
        self._applying_auto_department = True
        try:
            self.personal_department_combo.setCurrentText(department)
        finally:
            self._applying_auto_department = False
        self.employee_department_detection["mode"] = "auto"
        self.employee_department_detection["manual_department"] = self.personal_department_combo.currentText().strip()
        if not silent:
            self.log_message(f"✅ 已采用自动识别部门：{department}")
        self.on_personal_department_change()
        self.refresh_summary()

    def mark_manual_department_assignment(self, refresh_only: bool = False):
        department = self.personal_department_combo.currentText().strip()
        if not department:
            return
        self.employee_department_detection = {
            "mode": "manual",
            "manual_department": department,
            "department": department,
            "confidence": 1.0,
            "confidence_percent": 100.0,
            "reason": "用户人工标注。",
            "evidence": ["人工标注"],
        }
        self.department_detection_status_label.setText(f"当前采用人工标注部门：{department}")
        self.department_detection_reason_label.setText("人工标注会覆盖自动识别结果。")
        if not refresh_only:
            self.log_message(f"✍️ 已保存人工标注部门：{department}")
        self.refresh_summary()

    def auto_detect_local_models(self):
        self.local_detect_label.setText("正在扫描 Ollama、LM Studio 和常见 OpenAI 兼容本地服务...")
        self.log_message("🔎 开始自动识别本地模型...")
        self.local_detect_btn.setEnabled(False)
        thread = QThread(self)
        worker = LocalModelWorker(self.local_host_edit.text().strip(), self.local_type_combo.currentText().strip())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.handle_local_model_detection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda _result=None, _error_text='', w=worker: self._cleanup_worker(w))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread))
        self._threads.append(thread)
        self._workers.append(worker)
        thread.start()

    def handle_local_model_detection(self, models: list[dict[str, str]], error_text: str = ""):
        self.local_detect_btn.setEnabled(True)
        if error_text:
            self.local_detect_label.setText(f"本地模型识别失败：{error_text}")
            self.log_message(f"❌ 本地模型识别失败：{error_text}")
            return
        self.detected_local_models = models
        if not models:
            self.local_detect_label.setText("未识别到本地模型。请先启动 Ollama，或在 LM Studio 中启动 Local Server，然后再次点击自动识别。")
            self.log_message("⚠️ 未识别到本地模型。")
            return
        model_names: list[str] = []
        for item in models:
            name = item.get("model_name", "")
            if name and name not in model_names:
                model_names.append(name)
        self.local_model_combo.clear()
        self.local_model_combo.addItems(model_names)
        self.apply_detected_local_model(models[0])
        preview = "；".join(f"{item.get('source')}：{item.get('model_name')}" for item in models[:5])
        suffix = f"；另有 {len(models) - 5} 个" if len(models) > 5 else ""
        self.local_detect_label.setText(f"已识别 {len(models)} 个本地模型，已自动选择：{models[0].get('model_name')}。{preview}{suffix}")
        self.log_message(f"✅ 已识别本地模型：{preview}{suffix}")

    def apply_detected_local_model(self, model_info: dict[str, str]):
        model_name = model_info.get("model_name", "")
        model_type = model_info.get("model_type", "ollama")
        host = model_info.get("host", "")
        if model_name:
            self.local_model_combo.setCurrentText(model_name)
        if model_type in {"ollama", "lm-studio", "其他"}:
            self.local_type_combo.setCurrentText(model_type)
        else:
            self.local_type_combo.setCurrentText("其他")
        if host:
            self.local_host_edit.setText(host)

    def run_config_health_check_ui(self):
        payload = self.build_request_payload()
        report = run_config_health_check(
            payload,
            project_root=Path.cwd(),
            model_test_ok=self.last_model_test_ok,
            model_test_message=self.last_model_test_message,
        )
        text = format_health_report(report)
        if hasattr(self, "health_report_text"):
            self.health_report_text.setPlainText(text)
        status = report.get("status")
        summary = report.get("summary", "")
        if hasattr(self, "health_status_label"):
            self.health_status_label.setText(summary)
        self.log_message(f"🩺 配置体检完成：{summary}")
        if status == "error":
            self.show_error_dialog("配置体检发现问题", summary)
        else:
            self.show_info_dialog("配置体检完成", summary)

    def on_model_test_button_clicked(self):
        if self.active_model_test_worker is not None:
            self.cancel_model_connection_test()
        else:
            self.test_model_connection()

    def test_model_connection(self):
        payload = {
            "model_type": self.model_type(),
            "provider": self.provider_combo.currentText().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "api_base": self.api_base_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "local_model_type": self.local_type_combo.currentText().strip(),
            "local_model_name": self.local_model_combo.currentText().strip(),
            "local_host": self.local_host_edit.text().strip(),
        }
        self.model_test_run_id += 1
        run_id = self.model_test_run_id
        self.model_test_btn.setText("取消检查")
        self.model_test_btn.setEnabled(True)
        self.model_test_label.setText("正在检查模型连接...")
        self.last_model_test_ok = None
        self.last_model_test_message = ""
        self.log_message("🔌 开始检查模型连接...")
        # Do not use QThread for this small network probe.  The
        # model connection worker used moveToThread() and could crash on some Windows/PySide6
        # setups when the worker finished while a themed dialog was being opened.
        # A plain Python daemon thread plus Qt signals is simpler here: the worker only
        # performs blocking urllib calls and emits signals back to the GUI thread.
        worker = ModelConnectionTestWorker(payload)
        self.active_model_test_worker = worker
        self._workers.append(worker)
        worker.status.connect(lambda message, rid=run_id: self.handle_model_test_status(message, rid))
        worker.finished.connect(lambda ok, message, rid=run_id: self.handle_model_connection_test(ok, message, rid))
        worker.finished.connect(lambda _ok=False, _message='', w=worker: self._cleanup_worker(w))

        thread = threading.Thread(target=worker.run, name=f"model-connection-check-{run_id}", daemon=True)
        self.active_model_test_thread = thread
        self._python_threads.append(thread)
        self.model_test_timeout_timer.start(30000)
        thread.start()

    def handle_model_test_status(self, message: str, run_id: int):
        if run_id != self.model_test_run_id or self.active_model_test_worker is None:
            return
        self.model_test_label.setText(message)
        self.log_message(f"🔎 {message}")

    def cancel_model_connection_test(self):
        worker = self.active_model_test_worker
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
        self.model_test_timeout_timer.stop()
        self.model_test_label.setText("模型连接检查已取消。")
        self.last_model_test_ok = False
        self.last_model_test_message = "模型连接检查已取消。"
        self.model_test_btn.setText("检查模型连接")
        self.active_model_test_worker = None
        self.active_model_test_thread = None
        self._cleanup_python_threads()
        self.log_message("⏹️ 已取消模型连接检查。")

    def handle_model_test_timeout(self):
        worker = self.active_model_test_worker
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
        self.model_test_label.setText("模型连接检查超时，请检查网络、代理、API Base 或模型服务状态。")
        self.last_model_test_ok = False
        self.last_model_test_message = "模型连接检查超时。"
        self.model_test_btn.setText("检查模型连接")
        self.active_model_test_worker = None
        self.active_model_test_thread = None
        self._cleanup_python_threads()
        self.log_message("⏱️ 模型连接检查超时。")
        self.show_error_dialog("连接检查超时", "模型连接检查超过 30 秒未完成。\n\n请检查网络、代理、API Base、API Key 或模型服务状态。")

    def handle_model_connection_test(self, ok: bool, message: str, run_id: int | None = None):
        if run_id is not None and run_id != self.model_test_run_id:
            return
        if self.active_model_test_worker is None:
            return
        self.model_test_timeout_timer.stop()
        self.model_test_btn.setText("检查模型连接")
        self.active_model_test_worker = None
        self.active_model_test_thread = None
        self._cleanup_python_threads()
        self.last_model_test_ok = bool(ok)
        self.last_model_test_message = message
        if ok:
            self.model_test_label.setText(message)
            self.log_message(f"✅ 模型连接检查通过：{message}")
            QTimer.singleShot(0, lambda msg=message: self.show_info_dialog("连接可用", msg))
        else:
            self.model_test_label.setText(message)
            self.log_message(f"❌ 模型连接检查失败：{message}")
            QTimer.singleShot(0, lambda msg=message: self.show_error_dialog("连接失败", msg))

    def _cleanup_worker(self, worker: QObject):
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def _cleanup_thread(self, thread: QThread):
        try:
            self._threads.remove(thread)
        except ValueError:
            pass

    def _cleanup_python_threads(self):
        self._python_threads = [thread for thread in self._python_threads if thread.is_alive()]

    # ---------- payload, validation, config ----------
    def build_request_payload(self) -> dict:
        self.refresh_org_detection()
        profile_paths = self.profile_file_paths()
        model_paths = self.model_file_paths()
        payload = {
            "version": VERSION,
            "company_name": self.company_name_edit.text().strip(),
            "company_slug": normalize_company_slug(self.company_slug_edit.text().strip(), self.company_name_edit.text()),
            "template": (lambda t: {"id": t.id, "name": t.name} if t else {})(get_template_by_name(self.template_combo.currentText()) if hasattr(self, "template_combo") else None),
            "model_type": self.model_type(),
            "provider": self.provider_combo.currentText().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "api_base": self.api_base_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "local_model_type": self.local_type_combo.currentText().strip(),
            "local_model_name": self.local_model_combo.currentText().strip(),
            "local_host": self.local_host_edit.text().strip(),
            "temperature": self.temperature_edit.text().strip(),
            "company_files": self.company_files[:],
            "organization": {
                "selected_departments": self.selected_departments(),
                "custom_departments": self.custom_departments(),
                "org_notes": self.org_notes_edit.text().strip(),
                "use_uploaded_org_chart": bool(self.org_chart_files),
                "org_chart_files": self.org_chart_files[:],
            },
            "personal_distillation": {
                "enabled": bool(self.personal_enabled_check.isChecked()) and not bool(self.organization_members),
                "file_path": profile_paths[0] if profile_paths else "",
                "file_paths": profile_paths,
                "model_file_path": model_paths[0] if model_paths else "",
                "model_file_paths": model_paths,
                "level": self.personal_level_combo.currentText().strip(),
                "department": self.personal_department_combo.currentText().strip(),
                "position": self.personal_position_combo.currentText().strip(),
                "role_notes": self.personal_notes_edit.toPlainText().strip(),
                "has_peer_models": bool(self.has_peer_models_check.isChecked()),
                "department_assignment": self.build_department_assignment_payload(),
            },
            "organization_members": self.organization_members[:],
            "confirmation_notes": self.confirmation_notes_edit.toPlainText().strip(),
            "clean_output": bool(getattr(self, "clean_output_check", None) and self.clean_output_check.isChecked()),
        }
        return payload

    def refresh_summary(self):
        if not hasattr(self, "summary_text"):
            return
        payload = self.build_request_payload()
        lines = [
            f"=== 算疏智合 v{VERSION} 配置摘要 ===",
            f"界面版本: {ROUND}",
            f"公司名称: {payload['company_name'] or '未填写'}",
            f"公司标识: {payload['company_slug'] or '未填写'}",
            f"生成前清理旧产物: {'是' if payload.get('clean_output') else '否'}",
            f"项目模板: {payload.get('template', {}).get('name') or '未选择'}",
            f"模型类型: {payload['model_type']}",
            "",
            "[资料上传]",
            f"资料文件数: {len(payload['company_files'])}",
        ]
        if payload["company_files"]:
            for file_path in payload["company_files"][:8]:
                lines.append(f"- {Path(file_path).name}")
            if len(payload["company_files"]) > 8:
                lines.append(f"- ... 其余 {len(payload['company_files']) - 8} 个文件")
        else:
            lines.append("- 暂未上传文件")

        org = payload["organization"]
        lines.extend([
            "",
            "[组织架构]",
            f"组织架构图识别: {'是' if org.get('use_uploaded_org_chart') else '否'}",
            f"标准部门勾选数: {len(org['selected_departments'])}",
            f"自定义部门: {', '.join(org['custom_departments']) if org['custom_departments'] else '无'}",
            f"组织备注: {org['org_notes'] or '无'}",
        ])
        if org.get("org_chart_files"):
            lines.append("组织架构资料: " + "，".join(Path(path).name for path in org["org_chart_files"]))

        members = payload.get("organization_members", [])
        lines.extend([
            "",
            "[组织成员插入 / 组织人员蒸馏]",
            f"是否启用: {'是' if payload['personal_distillation']['enabled'] or members else '否'}",
            f"已插入成员数: {len(members)}",
        ])
        if members:
            for idx, member in enumerate(members, start=1):
                assignment = member.get("department_assignment", {}) or {}
                lines.extend([
                    f"- {idx}. {member.get('member_name') or member.get('position') or '未命名成员'}",
                    f"  层级/部门/岗位: {member.get('level') or '未设置'} / {member.get('department') or assignment.get('final_department') or '未设置'} / {member.get('position') or '未设置'}",
                    f"  类型/权限: {member.get('member_type') or '组织成员'} / {member.get('authority_level') or 'advisory'}",
                    f"  资料: {format_file_names(merge_file_path_values(member.get('profile_file_paths'), member.get('file_paths'), member.get('profile_file_path'), member.get('file_path')))}；Skill: {format_file_names(merge_file_path_values(member.get('skill_file_paths'), member.get('model_file_paths'), member.get('skill_file_path'), member.get('model_file_path')))}",
                ])
        elif payload["personal_distillation"]["enabled"]:
            personal = payload["personal_distillation"]
            lines.extend([
                f"岗位层级: {personal['level'] or '未设置'}",
                f"岗位部门: {personal['department'] or '未设置'}",
                f"部门归类方式: {personal.get('department_assignment', {}).get('mode', 'manual')}",
                f"自动识别部门: {personal.get('department_assignment', {}).get('detected_department') or '无'}",
                f"识别置信度: {personal.get('department_assignment', {}).get('confidence_percent', '') or '无'}",
                f"岗位名称: {personal['position'] or '未设置'}",
                f"已有同组织蒸馏模型: {'是' if personal['has_peer_models'] else '否（将尝试联网补全岗位信息）'}",
                f"组织人员资料文件: {format_file_names(merge_file_path_values(personal.get('file_paths'), personal.get('file_path')))}",
                f"组织人员蒸馏模型文件: {format_file_names(merge_file_path_values(personal.get('model_file_paths'), personal.get('model_file_path')))}",
            ])
        if payload["confirmation_notes"]:
            lines.extend(["", "[用户补充说明]", payload["confirmation_notes"]])
        self.summary_text.blockSignals(True)
        self.summary_text.setPlainText("\n".join(lines))
        self.summary_text.blockSignals(False)

    def validate_model_config(self) -> bool:
        if not self.company_name_edit.text().strip():
            self.show_error_dialog("输入错误", "请填写公司名称。")
            return False
        if not self.company_slug_edit.text().strip() or is_invalid_company_slug(self.company_slug_edit.text()):
            self.company_slug_edit.setText(normalize_company_slug(self.company_slug_edit.text(), self.company_name_edit.text()))
        if self.model_type() == "cloud":
            if not self.api_key_edit.text().strip():
                self.show_error_dialog("输入错误", "云端模式需要输入 API Key；如果使用本地模型，请切换到本地模式。")
                return False
            if not self.model_edit.text().strip():
                self.show_error_dialog("输入错误", "请输入模型名称。")
                return False
        else:
            if not self.local_model_combo.currentText().strip():
                self.show_error_dialog("输入错误", "请输入本地模型名称。")
                return False
            if not self.local_host_edit.text().strip():
                self.show_error_dialog("输入错误", "请输入本地模型服务地址。")
                return False
        return True

    def validate_company_files(self) -> bool:
        if self.company_files:
            return True
        return self.show_confirm_dialog("提示", "当前没有上传公司资料文件，仍然继续吗？")

    def validate_organization(self) -> bool:
        if self.org_chart_files:
            return True
        selected = self.selected_departments()
        if len(selected) < 4:
            return self.show_confirm_dialog("提示", "当前选择的标准部门较少，可能影响组织架构完整度。仍然继续吗？")
        return True

    def validate_personnel_distillation(self) -> bool:
        if not self.personal_enabled_check.isChecked():
            return True
        if self.organization_members:
            return True
        if not self.personal_position_combo.currentText().strip() and not self.profile_file_paths() and not self.model_file_paths():
            self.show_error_dialog("输入错误", "启用组织成员插入时，请至少填写岗位名称，或选择员工资料 / 员工 Skill 文件。")
            return False
        return True

    def validate_inputs(self) -> bool:
        return all([self.validate_model_config(), self.validate_company_files(), self.validate_organization(), self.validate_personnel_distillation()])

    def save_config(self):
        if not self.save_config_check.isChecked():
            return
        payload = self.build_request_payload()
        config = {
            "version": VERSION,
            "ui": "pyside6-qt-v0.1.0",
            "model_type": payload["model_type"],
            "provider": payload["provider"],
            "last_company_name": payload["company_name"],
            "last_company_slug": payload["company_slug"],
            "online": {"api_key": "", "api_base": payload["api_base"], "model": payload["model"]},
            "local": {"model_type": payload["local_model_type"], "model_name": payload["local_model_name"], "host": payload["local_host"], "temperature": float(payload["temperature"] or 0.7)},
            "last_request": redact_secrets(payload),
        }
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log_message("💾 配置已保存到本地（API Key 已脱敏，不写入 config.json）")

    def load_config(self):
        if not CONFIG_PATH.exists():
            self.refresh_summary()
            return
        try:
            config = load_json_with_encoding_fallback(CONFIG_PATH)
            model_type = config.get("model_type", "cloud")
            self.cloud_radio.setChecked(model_type != "local")
            self.local_radio.setChecked(model_type == "local")
            self.provider_combo.setCurrentText(config.get("provider", "deepseek"))
            self.company_name_edit.setText(config.get("last_company_name", ""))
            self.company_slug_edit.setText(config.get("last_company_slug", ""))
            online = config.get("online", {})
            saved_key = online.get("api_key", "")
            self.api_key_edit.setText("" if str(saved_key).startswith("***") else saved_key)
            self.api_base_edit.setText(online.get("api_base", "https://api.deepseek.com"))
            self.model_edit.setText(online.get("model", "deepseek-chat"))
            local = config.get("local", {})
            self.local_type_combo.setCurrentText(local.get("model_type", "ollama"))
            self.local_model_combo.setCurrentText(local.get("model_name", "qwen2.5:7b"))
            self.local_host_edit.setText(local.get("host", "http://localhost:11434"))
            self.temperature_edit.setText(str(local.get("temperature", 0.7)))
            last_request = config.get("last_request", {})
            self.company_files = list(last_request.get("company_files", []))
            self.files_list.clear()
            for item in self.company_files:
                self.files_list.addItem(item)
            org = last_request.get("organization", {})
            self.org_notes_edit.setText(org.get("org_notes", self.org_notes_edit.text()))
            self.custom_departments_list = list(org.get("custom_departments", []))
            self.rebuild_custom_departments_list()
            selected = set(org.get("selected_departments", DEFAULT_DEPARTMENTS))
            for department, cb in self.department_vars.items():
                cb.setChecked(department in selected)
            self.refresh_org_detection()
            self.update_files_hint()
            personal = last_request.get("personal_distillation", {})
            self.personal_enabled_check.setChecked(personal.get("enabled", False))
            self.set_profile_file_paths(merge_file_path_values(personal.get("file_paths"), personal.get("profile_file_paths"), personal.get("file_path"), personal.get("profile_file_path")))
            self.set_model_file_paths(merge_file_path_values(personal.get("model_file_paths"), personal.get("skill_file_paths"), personal.get("model_file_path"), personal.get("skill_file_path")))
            self.personal_level_combo.setCurrentText(personal.get("level", ORG_LEVELS[6]))
            self.personal_department_combo.setCurrentText(personal.get("department", DEFAULT_DEPARTMENTS[5]))
            self.personal_position_combo.setCurrentText(personal.get("position", DEPARTMENT_ROLE_MAP[DEFAULT_DEPARTMENTS[5]][0]))
            assignment = personal.get("department_assignment", {}) or {}
            self.employee_department_detection = assignment
            if assignment.get("detected_department"):
                self.department_detection_status_label.setText(f"上次识别结果：{assignment.get('detected_department')}，置信度 {assignment.get('confidence_percent', '')}")
                self.department_detection_reason_label.setText(f"依据：{assignment.get('reason', '')}")
            self.has_peer_models_check.setChecked(personal.get("has_peer_models", False))
            self.personal_notes_edit.setPlainText(personal.get("role_notes", ""))
            self.organization_members = list(last_request.get("organization_members", []))
            self.refresh_organization_members_list()
            self.confirmation_notes_edit.setPlainText(last_request.get("confirmation_notes", ""))
            self.log_message("✅ 已加载本地配置")
        except Exception as exc:
            self.log_message(f"⚠️ 加载配置失败：{exc}")
        finally:
            self.on_model_type_change()
            self.update_personal_section_state()
            self.refresh_summary()


    # ---------- project file operations ----------
    def update_project_label(self):
        if not hasattr(self, "current_project_label"):
            return
        if self.current_project_path:
            self.current_project_label.setText(f"当前项目：{self.current_project_path.name}")
        else:
            self.current_project_label.setText("当前项目：未保存")

    def refresh_recent_projects_list(self):
        if not hasattr(self, "recent_project_list"):
            return
        self.recent_project_list.clear()
        for path_text in self.app_settings.recent_projects():
            path = Path(path_text)
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.recent_project_list.addItem(item)
        self.recent_project_list.setVisible(self.recent_project_list.count() > 0)

    def open_recent_project_item(self, item: QListWidgetItem):
        path_text = item.data(Qt.ItemDataRole.UserRole)
        if path_text:
            self.open_project_path(Path(str(path_text)))

    def new_project(self):
        if not self.show_confirm_dialog("新建项目", "将清空当前界面中的项目配置。请确认当前内容已保存。", confirm_text="新建", cancel_text="取消"):
            return
        self.clear_workspace()
        self.log_message("🆕 已新建空白项目")

    def clear_workspace(self):
        self.current_project_path = None
        self.company_name_edit.clear()
        self.company_slug_edit.clear()
        self.cloud_radio.setChecked(True)
        self.provider_combo.setCurrentText("deepseek")
        self.api_key_edit.clear()
        self.api_base_edit.setText("https://api.deepseek.com")
        self.model_edit.setText("deepseek-chat")
        self.local_type_combo.setCurrentText("ollama")
        self.local_model_combo.setCurrentText("qwen2.5:7b")
        self.local_host_edit.setText("http://localhost:11434")
        self.temperature_edit.setText("0.7")
        self.company_files = []
        self.files_list.clear()
        self.custom_departments_list = []
        self.rebuild_custom_departments_list()
        for department, cb in self.department_vars.items():
            cb.setChecked(department in DEFAULT_DEPARTMENTS)
        self.org_notes_edit.setText("参考大型公司主流部门设置，可按业务需要增删。")
        self.personal_enabled_check.setChecked(False)
        self.set_profile_file_paths([])
        self.set_model_file_paths([])
        self.member_name_edit.clear()
        self.personal_level_combo.setCurrentText(ORG_LEVELS[6])
        self.personal_department_combo.setCurrentText(DEFAULT_DEPARTMENTS[5])
        self.personal_position_combo.setCurrentText(DEPARTMENT_ROLE_MAP[DEFAULT_DEPARTMENTS[5]][0])
        self.personal_notes_edit.clear()
        self.has_peer_models_check.setChecked(False)
        self.organization_members = []
        self.refresh_organization_members_list()
        self.confirmation_notes_edit.clear()
        self.preview_md_path = None
        self.preview_json_path = None
        self.current_output_dir = None
        if hasattr(self, "result_group"):
            self.result_group.setVisible(False)
        self.refresh_org_detection()
        self.update_files_hint()
        self.update_personal_section_state()
        self.update_project_label()
        self.refresh_summary()
        self.show_step(0)

    def save_project(self):
        if self.current_project_path:
            self.write_project_file(self.current_project_path)
        else:
            self.save_project_as()

    def save_project_as(self):
        default_name = default_project_filename(self.company_name_edit.text().strip(), self.company_slug_edit.text().strip())
        projects_dir = Path(__file__).resolve().parent / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "保存算疏智合项目",
            str(projects_dir / default_name),
            "算疏智合项目 (*.sszh.json);;JSON 文件 (*.json)",
        )
        if not filename:
            return
        self.write_project_file(Path(filename))

    def write_project_file(self, path: Path):
        try:
            payload = self.build_request_payload()
            saved_path = save_project_file(path, payload, project_name=payload.get("company_name") or "未命名项目")
            self.current_project_path = saved_path
            self.app_settings.save_recent_project(saved_path)
            self.app_settings.save_last_project(saved_path)
            self.refresh_recent_projects_list()
            self.update_project_label()
            self.log_message(f"💾 项目已保存：{saved_path}")
            self.show_info_dialog("项目已保存", f"项目配置已保存。\n{saved_path}\n\nAPI Key 不会写入项目文件，重新打开项目后请按需重新输入。")
        except Exception as exc:
            self.show_error_dialog("保存项目失败", str(exc))

    def open_project_dialog(self):
        projects_dir = Path(__file__).resolve().parent / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "打开算疏智合项目",
            str(projects_dir),
            "算疏智合项目 (*.sszh.json);;JSON 文件 (*.json);;所有文件 (*)",
        )
        if filename:
            self.open_project_path(Path(filename))

    def open_project_path(self, path: Path):
        try:
            if not path.exists():
                self.show_error_dialog("打开项目失败", f"项目文件不存在：\n{path}")
                return
            document, payload = load_project_file(path)
            self.apply_request_payload(payload)
            self.current_project_path = path
            self.app_settings.save_recent_project(path)
            self.app_settings.save_last_project(path)
            self.refresh_recent_projects_list()
            self.update_project_label()
            name = document.get("project_name") or payload.get("company_name") or path.name
            self.log_message(f"📂 已打开项目：{path}")
            self.show_info_dialog("项目已打开", f"已载入：{name}\n\n为保护密钥安全，项目文件不会恢复 API Key，请在模型配置页按需重新输入。")
            self.show_step(0)
        except Exception as exc:
            self.show_error_dialog("打开项目失败", str(exc))

    def apply_request_payload(self, payload: dict):
        self.cloud_radio.setChecked(payload.get("model_type", "cloud") != "local")
        self.local_radio.setChecked(payload.get("model_type", "cloud") == "local")
        template_meta = payload.get("template", {}) or {}
        template_name = template_meta.get("name") if isinstance(template_meta, dict) else ""
        if hasattr(self, "template_combo") and template_name:
            idx = self.template_combo.findText(str(template_name))
            if idx >= 0:
                self.template_combo.setCurrentIndex(idx)
                self.update_template_preview()
        self.provider_combo.setCurrentText(payload.get("provider", "deepseek"))
        self.company_name_edit.setText(payload.get("company_name", ""))
        self.company_slug_edit.setText(payload.get("company_slug", ""))
        api_key = payload.get("api_key", "")
        self.api_key_edit.setText("" if str(api_key).startswith("***") else str(api_key or ""))
        self.api_base_edit.setText(payload.get("api_base", "https://api.deepseek.com"))
        self.model_edit.setText(payload.get("model", "deepseek-chat"))
        self.local_type_combo.setCurrentText(payload.get("local_model_type", "ollama"))
        self.local_model_combo.setCurrentText(payload.get("local_model_name", "qwen2.5:7b"))
        self.local_host_edit.setText(payload.get("local_host", "http://localhost:11434"))
        self.temperature_edit.setText(str(payload.get("temperature", 0.7)))

        self.company_files = list(payload.get("company_files", []) or [])
        self.files_list.clear()
        for item in self.company_files:
            self.files_list.addItem(str(item))

        org = payload.get("organization", {}) or {}
        self.org_notes_edit.setText(org.get("org_notes", "参考大型公司主流部门设置，可按业务需要增删。"))
        self.custom_departments_list = list(org.get("custom_departments", []) or [])
        self.rebuild_custom_departments_list()
        selected = set(org.get("selected_departments", DEFAULT_DEPARTMENTS) or [])
        for department, cb in self.department_vars.items():
            cb.setChecked(department in selected)
        self.refresh_org_detection()
        self.update_files_hint()

        personal = payload.get("personal_distillation", {}) or {}
        members = list(payload.get("organization_members", []) or [])
        self.personal_enabled_check.setChecked(bool(personal.get("enabled", False) or members))
        self.set_profile_file_paths(merge_file_path_values(personal.get("file_paths"), personal.get("profile_file_paths"), personal.get("file_path"), personal.get("profile_file_path")))
        self.set_model_file_paths(merge_file_path_values(personal.get("model_file_paths"), personal.get("skill_file_paths"), personal.get("model_file_path"), personal.get("skill_file_path")))
        self.personal_level_combo.setCurrentText(personal.get("level", ORG_LEVELS[6]))
        self.personal_department_combo.setCurrentText(personal.get("department", DEFAULT_DEPARTMENTS[5]))
        self.personal_position_combo.setCurrentText(personal.get("position", DEPARTMENT_ROLE_MAP[DEFAULT_DEPARTMENTS[5]][0]))
        assignment = personal.get("department_assignment", {}) or {}
        self.employee_department_detection = assignment
        if assignment.get("detected_department"):
            self.department_detection_status_label.setText(f"上次识别结果：{assignment.get('detected_department')}，置信度 {assignment.get('confidence_percent', '')}")
            self.department_detection_reason_label.setText(f"依据：{assignment.get('reason', '')}")
        else:
            self.department_detection_status_label.setText("尚未识别员工资料所属部门。")
            self.department_detection_reason_label.setText("")
        self.has_peer_models_check.setChecked(bool(personal.get("has_peer_models", False)))
        self.personal_notes_edit.setPlainText(personal.get("role_notes", ""))
        self.organization_members = members
        self.refresh_organization_members_list()
        self.confirmation_notes_edit.setPlainText(payload.get("confirmation_notes", ""))

        self.on_model_type_change()
        self.update_personal_section_state()
        self.refresh_summary()

    # ---------- run tasks ----------
    def validate_preview_inputs(self) -> bool:
        if not self.company_name_edit.text().strip():
            self.show_error_dialog("输入错误", "请填写公司名称。")
            return False
        if not self.company_slug_edit.text().strip() or is_invalid_company_slug(self.company_slug_edit.text()):
            self.company_slug_edit.setText(normalize_company_slug(self.company_slug_edit.text(), self.company_name_edit.text()))
        return all([self.validate_company_files(), self.validate_organization(), self.validate_personnel_distillation()])

    def generate_preview(self):
        if not self.validate_preview_inputs():
            return
        self.save_config()
        self.generate_local_preview()

    def generate_local_preview(self):
        self.active_task_mode = "preview"
        self.current_task_log_path = create_task_log_path("preview")
        self.last_task_log_path = self.current_task_log_path
        self.log_message(f"🧾 本次预览日志：{self.current_task_log_path}")
        self.set_busy(True)
        self.set_task_progress("整理配置预览", 18)
        try:
            payload = self.build_request_payload()
            preview_slug = payload.get("company_slug") or normalize_company_slug("", payload.get("company_name")) or "company"
            preview_dir = Path(__file__).resolve().parent / "preview_reports"
            preview_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            md_path = preview_dir / f"{preview_slug}-local-preview-{stamp}.md"
            json_path = preview_dir / f"{preview_slug}-local-preview-{stamp}.json"
            self.set_task_progress("生成本地预览报告", 55)
            content = self.compose_local_preview_markdown(payload)
            md_path.write_text(content, encoding="utf-8")
            json_path.write_text(json.dumps(redact_secrets(payload), ensure_ascii=False, indent=2), encoding="utf-8")
            self.preview_md_path = md_path
            self.preview_json_path = json_path
            self.summary_text.setPlainText(content)
            self.show_step(4)
            self.set_task_progress("预览已完成", 100)
            self.status_label.setText("预览完成：本地配置报告已生成，未调用模型。")
            self.show_info_dialog("预览完成", "配置预览已生成。该步骤未调用模型，可检查后再确认生成。")
        except Exception as exc:
            self.set_task_progress("预览失败", 0)
            self.show_error_dialog("预览失败", str(exc))
        finally:
            self.set_busy(False)

    def compose_local_preview_markdown(self, payload: dict) -> str:
        org = payload.get("organization", {})
        personal = payload.get("personal_distillation", {})
        members = payload.get("organization_members", []) or []
        lines = [
            f"# {payload.get('company_name') or '未命名公司'} · 生成配置预览",
            "",
            "> 本预览由 GUI 本地生成，不调用云端或本地模型；正式生成时才会执行模型调用。",
            "",
            "## 1. 基础信息",
            f"- 公司名称：{payload.get('company_name') or '未填写'}",
            f"- 公司标识：{payload.get('company_slug') or '未填写'}",
            f"- 模型模式：{payload.get('model_type')}",
            f"- 服务商 / 模型：{payload.get('provider') or payload.get('local_model_type')} / {payload.get('model') if payload.get('model_type') == 'cloud' else payload.get('local_model_name')}",
            "",
            "## 2. 资料文件",
            f"- 公司资料文件数：{len(payload.get('company_files', []))}",
        ]
        for path in payload.get("company_files", [])[:10]:
            lines.append(f"  - {Path(path).name}")
        if len(payload.get("company_files", [])) > 10:
            lines.append(f"  - 其余 {len(payload.get('company_files', [])) - 10} 个文件略")
        lines.extend([
            "",
            "## 3. 组织架构",
            f"- 标准部门：{', '.join(org.get('selected_departments', [])) or '未选择'}",
            f"- 自定义部门：{', '.join(org.get('custom_departments', [])) or '无'}",
            f"- 组织架构资料：{format_file_names(org.get('org_chart_files', []))}",
            f"- 组织备注：{org.get('org_notes') or '无'}",
            "",
            "## 4. 组织人员 / 插槽成员",
            f"- 已插入组织成员数：{len(members)}",
        ])
        if members:
            for index, member in enumerate(members, start=1):
                lines.append(f"  - {index}. {member.get('member_name') or member.get('position') or '未命名成员'}｜{member.get('department') or '未设置部门'}｜{member.get('position') or '未设置岗位'}")
        else:
            lines.extend([
                f"- 单人蒸馏启用：{'是' if personal.get('enabled') else '否'}",
                f"- 岗位层级 / 部门 / 岗位：{personal.get('level') or '未设置'} / {personal.get('department') or '未设置'} / {personal.get('position') or '未设置'}",
                f"- 员工资料：{format_file_names(merge_file_path_values(personal.get('file_paths'), personal.get('file_path')))}",
                f"- 员工 Skill：{format_file_names(merge_file_path_values(personal.get('model_file_paths'), personal.get('model_file_path')))}",
            ])
        lines.extend([
            "",
            "## 5. 正式生成将产出",
            "- boss-skill.md",
            "- employee-collective-skill.md",
            "- company-code.md",
            "- SKILL.md",
            "- strategy-vision.md",
            "- career-path.md（如启用组织人员预测）",
            "- organization-members.md（如插入组织成员）",
            "- Codex / Claude Code Agent 兼容导出",
            "",
            "## 6. 最终补充说明",
            payload.get("confirmation_notes") or "无",
        ])
        return "\n".join(lines)

    def start_generation(self):
        if not self.validate_inputs():
            return
        if not self.show_confirm_dialog("确认生成", "将根据当前确认配置开始正式生成，是否继续？", confirm_text="开始生成"):
            return
        self.save_config()
        self.run_task(mode="generate")

    def run_task(self, mode: str):
        payload = self.build_request_payload()
        payload_dir = Path(__file__).resolve().parent / "runtime_requests"
        payload_dir.mkdir(exist_ok=True)
        request_path = payload_dir / f"{payload['company_slug'] or 'company'}-request.json"
        request_payload = dict(payload)
        if request_payload.get("model_type") == "cloud":
            request_payload["api_key"] = ""
        request_path.write_text(json.dumps(redact_secrets(request_payload), ensure_ascii=False, indent=2), encoding="utf-8")
        run_script = Path(__file__).resolve().parent / "run.py"
        cmd = [sys.executable, str(run_script), "--request-file", str(request_path)]
        if payload.get("clean_output"):
            cmd.append("--clean-output")
        if payload["model_type"] == "cloud":
            cmd.extend(["--api-key", payload["api_key"], "--api-base", payload["api_base"], "--model", payload["model"]])
        else:
            cmd.extend(["--local", "--local-model", payload["local_model_name"], "--local-host", payload["local_host"], "--temperature", payload["temperature"]])
        self.active_task_mode = mode
        self.active_task_started_at = datetime.now()
        self.current_task_log_path = create_task_log_path(mode)
        self.last_task_log_path = self.current_task_log_path
        self.log_message(f"🧾 本次任务日志：{self.current_task_log_path}")
        self.set_busy(True)
        self.set_task_progress("准备启动生成进程", 8)
        thread = QThread(self)
        timeout_seconds = 20 * 60
        worker = SubprocessWorker(cmd, mode, Path(__file__).resolve().parent, timeout_seconds=timeout_seconds)
        self.active_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log_message)
        worker.progress.connect(self.set_task_progress)
        worker.finished.connect(self.handle_task_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread))
        self._threads.append(thread)
        thread.start()

    def cancel_active_task(self):
        if self.active_worker is not None:
            self.set_task_progress("正在取消任务", max(self.task_progress_value, 10))
            self.active_worker.cancel_process()

    def set_task_progress(self, label: str, value: int):
        self.task_progress_value = max(0, min(100, int(value)))
        self.task_progress_label.setText(label)
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(self.task_progress_value)

    def advance_task_progress(self):
        if self.active_task_mode != "generate":
            return
        current = self.task_progress_value
        if current < 35:
            self.set_task_progress("读取资料并准备上下文", current + 3)
        elif current < 72:
            self.set_task_progress("调用模型生成 Skill 内容", current + 2)
        elif current < 88:
            self.set_task_progress("整理输出文件", current + 1)

    def set_busy(self, busy: bool):
        for btn in [self.prev_btn, self.next_btn, self.preview_btn, self.finish_btn]:
            btn.setEnabled(not busy)
        if hasattr(self, "cancel_task_btn"):
            self.cancel_task_btn.setVisible(bool(busy and self.current_step == len(self.steps) - 1))
            self.cancel_task_btn.setEnabled(busy)
        if busy:
            if self.active_task_mode == "preview":
                label = "正在生成配置预览"
            else:
                label = "正在生成 Skill 文件"
            self.task_progress_label.setText(label)
            self.task_progress_label.setVisible(True)
            self.task_progress.setVisible(True)
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(0)
            self.task_progress_value = 0
            self.preview_btn.setText("处理中...")
            self.finish_btn.setText("处理中...")
            if self.active_task_mode == "generate":
                self.task_progress_timer.start()
        else:
            self.task_progress_timer.stop()
            self.preview_btn.setText("预览配置")
            self.finish_btn.setText("确认生成")
            self.active_worker = None
            self.active_task_mode = None
    def handle_task_finished(self, mode: str, output_lines: list[str], return_code: int, error_text: str):
        success = return_code == 0 and not error_text
        self.set_busy(False)
        self.task_progress.setValue(100 if success else 0)
        self.task_progress_label.setText("生成已完成" if success else "执行未完成")
        self.update_navigation()
        if error_text:
            self.log_message(f"❌ 执行异常：{error_text}")
            self.show_error_dialog("执行异常", error_text)
            return
        if return_code != 0:
            self.show_error_dialog("执行失败", "任务执行失败，请查看 run.log。")
            return
        if mode == "preview":
            self.handle_preview_success(output_lines)
        else:
            self.handle_generation_success(output_lines)

    def handle_preview_success(self, output_lines: list[str]):
        preview_md = None
        preview_json = None
        for line in output_lines:
            if line.startswith("PREVIEW_MD="):
                preview_md = Path(line.split("=", 1)[1])
            if line.startswith("PREVIEW_JSON="):
                preview_json = Path(line.split("=", 1)[1])
        self.preview_md_path = preview_md
        self.preview_json_path = preview_json
        if preview_md and preview_md.exists():
            content = read_text_with_encoding_fallback(preview_md)
            self.summary_text.setPlainText(content)
        self.show_step(4)
        self.status_label.setText("预览完成：可以继续补充说明或确认生成。")
        self.show_info_dialog("预览完成", "配置预览已生成，你可以继续补充说明后再确认生成。")

    def populate_result_viewer(self, output_dir: str | Path | None):
        if not output_dir:
            return
        out_path = Path(str(output_dir))
        if not out_path.exists():
            self.current_output_dir = out_path
            self.output_dir_label.setText(f"输出目录不存在或暂不可访问：{out_path}")
            self.result_group.setVisible(True)
            self.refresh_output_quality()
            return
        self.current_output_dir = out_path
        self.app_settings.save_recent_output_dir(out_path)
        self.output_dir_label.setText(f"输出目录：{out_path}")
        self.result_files_list.clear()
        self.result_preview.clear()

        priority = [
            "SKILL.md",
            "boss-skill.md",
            "employee-collective-skill.md",
            "company-code.md",
            "strategy-vision.md",
            "career-path.md",
            "organization-members.md",
            "generation-summary.md",
            "generation-request.json",
        ]
        files = [item for item in out_path.iterdir() if item.is_file()]
        ordered: list[Path] = []
        for name in priority:
            match = out_path / name
            if match.exists() and match.is_file():
                ordered.append(match)
        for item in sorted(files, key=lambda x: x.name.lower()):
            if item not in ordered:
                ordered.append(item)

        for item in ordered:
            list_item = QListWidgetItem(item.name)
            list_item.setData(Qt.ItemDataRole.UserRole, str(item))
            self.result_files_list.addItem(list_item)
        self.result_group.setVisible(True)
        if self.result_files_list.count() > 0:
            self.result_files_list.setCurrentRow(0)
        else:
            self.result_preview.setPlainText("输出目录中暂未发现可预览文件。")
        self.refresh_output_quality()

    def refresh_output_quality(self):
        if not hasattr(self, "quality_report"):
            return
        if not self.current_output_dir:
            self.quality_summary_label.setText("尚无可检查的输出目录。")
            self.quality_report.clear()
            return
        report = inspect_output_quality(self.current_output_dir)
        status = report.get("status")
        title = report.get("title") or "生成质量检查"
        summary = report.get("summary") or {}
        if status == "pass":
            self.quality_summary_label.setText(f"✅ {title}")
        elif status == "warn":
            self.quality_summary_label.setText(f"⚠️ {title} · 建议人工复核")
        else:
            self.quality_summary_label.setText(f"❌ {title} · 建议先修复后使用")
        self.quality_report.setPlainText(format_quality_report(report))

    def on_result_file_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None = None):
        if current is None:
            return
        path_text = current.data(Qt.ItemDataRole.UserRole)
        if not path_text:
            return
        path = Path(str(path_text))
        if not path.exists():
            self.result_preview.setPlainText("文件不存在或已被移动。")
            return
        if path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}:
            self.result_preview.setPlainText(f"{path.name} 不是文本文件，可点击“打开选中文件”查看。")
            return
        try:
            self.result_preview.setPlainText(read_text_with_encoding_fallback(path))
        except Exception as exc:
            self.result_preview.setPlainText(f"读取失败：{exc}")

    def selected_result_path(self) -> Path | None:
        item = self.result_files_list.currentItem() if hasattr(self, "result_files_list") else None
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return Path(str(data)) if data else None

    def open_output_directory(self):
        if self.current_output_dir and self.current_output_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_output_dir)))
        else:
            self.show_error_dialog("无法打开", "当前没有可打开的输出目录。")

    def copy_output_path(self):
        if self.current_output_dir:
            QApplication.clipboard().setText(str(self.current_output_dir))
            self.show_info_dialog("已复制", "输出目录路径已复制到剪贴板。")
        else:
            self.show_error_dialog("无法复制", "当前没有可复制的输出目录。")

    def open_selected_result_file(self):
        path = self.selected_result_path()
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.show_error_dialog("无法打开", "请先选择一个生成文件。")

    def handle_generation_success(self, output_lines: list[str]):
        output_dir = None
        for line in output_lines:
            if line.startswith("OUTPUT_DIR="):
                output_dir = line.split("=", 1)[1]
                break
        message = "Skill 文件已生成。"
        if output_dir:
            self.populate_result_viewer(output_dir)
            self.record_generation_history(output_dir)
            message += f"\n输出目录：{output_dir}"
        self.status_label.setText("生成完成：可以在生成结果区查看输出文件。")
        self.show_info_dialog("生成完成", message)

    # ---------- generation history ----------
    def record_generation_history(self, output_dir: str | Path):
        try:
            out_path = Path(str(output_dir))
            payload = self.build_request_payload()
            report = inspect_output_quality(out_path)
            files = sorted([item.name for item in out_path.iterdir() if item.is_file()], key=str.lower) if out_path.exists() else []
            warnings = [item.get("title", "") for item in report.get("items", []) if item.get("level") in {"warn", "fail"}]
            duration = None
            if self.active_task_started_at is not None:
                duration = round((datetime.now() - self.active_task_started_at).total_seconds(), 1)
            record = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "company_name": payload.get("company_name") or "",
                "company_slug": payload.get("company_slug") or "",
                "model_type": payload.get("model_type") or "",
                "model": payload.get("model") if payload.get("model_type") == "cloud" else payload.get("local_model_name"),
                "output_dir": str(out_path),
                "log_path": str(self.last_task_log_path or self.current_task_log_path or ""),
                "quality_status": report.get("status") or "unknown",
                "quality_title": report.get("title") or "",
                "quality_warnings": warnings[:20],
                "duration_seconds": duration,
                "files": files,
            }
            append_generation_record(record)
            self.refresh_generation_history()
            self.log_message("🕘 已记录生成历史")
        except Exception as exc:
            self.log_message(f"⚠️ 记录生成历史失败：{exc}")

    def refresh_generation_history(self):
        if not hasattr(self, "generation_history_list"):
            return
        try:
            self.generation_history_records = load_generation_history()
        except Exception as exc:
            self.generation_history_records = []
            self.generation_history_detail.setPlainText(f"读取生成历史失败：{exc}")
            return
        self.generation_history_list.clear()
        for index, record in enumerate(self.generation_history_records):
            item = QListWidgetItem(format_history_item(record))
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.generation_history_list.addItem(item)
        if self.generation_history_list.count() > 0:
            self.generation_history_list.setCurrentRow(0)
        else:
            self.generation_history_detail.setPlainText("暂无生成历史。完成一次正式生成后会自动记录。")

    def selected_generation_history_record(self) -> dict | None:
        if not hasattr(self, "generation_history_list"):
            return None
        item = self.generation_history_list.currentItem()
        if item is None:
            return None
        index = item.data(Qt.ItemDataRole.UserRole)
        try:
            return self.generation_history_records[int(index)]
        except Exception:
            return None

    def on_generation_history_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None = None):
        record = self.selected_generation_history_record()
        if record is None:
            return
        self.generation_history_detail.setPlainText(format_history_detail(record))

    def open_history_output_directory(self):
        record = self.selected_generation_history_record()
        output_dir = Path(str((record or {}).get("output_dir") or ""))
        if output_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir)))
        else:
            self.show_error_dialog("无法打开", "当前历史记录的输出目录不存在或已移动。")

    def open_history_log_file(self):
        record = self.selected_generation_history_record()
        log_path = Path(str((record or {}).get("log_path") or ""))
        if log_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))
        else:
            self.show_error_dialog("无法打开", "当前历史记录没有可打开的日志文件。")

    def copy_history_output_path(self):
        record = self.selected_generation_history_record()
        output_dir = str((record or {}).get("output_dir") or "")
        if output_dir:
            QApplication.clipboard().setText(output_dir)
            self.show_info_dialog("已复制", "历史输出目录路径已复制到剪贴板。")
        else:
            self.show_error_dialog("无法复制", "当前历史记录没有输出目录。")

    # ---------- navigation ----------
    def show_step(self, index: int):
        self.current_step = max(0, min(index, len(self.steps) - 1))
        self.stack.setCurrentIndex(self.current_step)
        if self.current_step == len(self.steps) - 1:
            self.refresh_summary()
        self.update_navigation()

    def next_step(self):
        if self.current_step == 0 and not self.validate_model_config():
            return
        if self.current_step == 1 and not self.validate_company_files():
            return
        if self.current_step == 2 and not self.validate_organization():
            return
        if self.current_step == 3 and not self.validate_personnel_distillation():
            return
        if self.current_step < len(self.steps) - 1:
            self.show_step(self.current_step + 1)

    def prev_step(self):
        if self.current_step > 0:
            self.show_step(self.current_step - 1)

    def goto_step(self, target_step: int):
        if target_step <= self.current_step:
            self.show_step(target_step)
        elif target_step == self.current_step + 1:
            self.next_step()
        else:
            self.show_info_dialog("提示", "请按顺序逐级完成前面的配置步骤。")

    def update_navigation(self):
        final_state = self.current_step == len(self.steps) - 1
        busy = self.active_task_mode is not None
        self.prev_btn.setEnabled((self.current_step > 0) and not busy)
        self.next_btn.setEnabled((self.current_step < len(self.steps) - 1) and not busy)
        self.next_btn.setText(f"下一步：{self.steps[self.current_step + 1]} ➜" if self.current_step < len(self.steps) - 1 else "已到最后一步")
        self.preview_btn.setVisible(final_state)
        self.finish_btn.setVisible(final_state)
        self.preview_btn.setEnabled(final_state and not busy)
        self.finish_btn.setEnabled(final_state and not busy)
        if hasattr(self, "cancel_task_btn"):
            self.cancel_task_btn.setVisible(final_state and busy)
            self.cancel_task_btn.setEnabled(busy)
        show_progress = final_state and (busy or self.task_progress.value() > 0)
        self.task_progress_label.setVisible(show_progress)
        self.task_progress.setVisible(show_progress)
        self.status_label.setText(f"当前步骤：{self.current_step + 1}/{len(self.steps)} · {self.steps[self.current_step]}")
        for idx, btn in enumerate(self.step_buttons):
            if idx < self.current_step:
                btn.setText(f"✓ {idx + 1}. {self.steps[idx]}")
                btn.setEnabled(True)
                btn.setProperty("step", "done")
            elif idx == self.current_step:
                btn.setText(f"▶ {idx + 1}. {self.steps[idx]}")
                btn.setEnabled(False)
                btn.setProperty("step", "current")
            elif idx == self.current_step + 1:
                btn.setText(f"{idx + 1}. {self.steps[idx]}")
                btn.setEnabled(True)
                btn.setProperty("step", "pending")
            else:
                btn.setText(f"{idx + 1}. {self.steps[idx]}")
                btn.setEnabled(False)
                btn.setProperty("step", "pending")
            btn.style().unpolish(btn)
            btn.style().polish(btn)


    # ---------- frameless window behavior ----------
    def changeEvent(self, event):  # pragma: no cover - GUI behavior
        super().changeEvent(event)
        if hasattr(self, "title_bar") and event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.sync_window_state()

    def eventFilter(self, watched, event):  # pragma: no cover - GUI behavior
        if PYSIDE_IMPORT_ERROR is None and isinstance(watched, QWidget) and watched.window() is self:
            event_type = event.type()
            if event_type == QEvent.Type.MouseMove:
                if not self.isMaximized() and hasattr(event, "globalPosition"):
                    self.update_resize_cursor(self.mapFromGlobal(event.globalPosition().toPoint()))
            elif event_type == QEvent.Type.MouseButtonPress:
                if (
                    not self.isMaximized()
                    and hasattr(event, "globalPosition")
                    and event.button() == Qt.MouseButton.LeftButton
                ):
                    edges = self.resize_edges_at(self.mapFromGlobal(event.globalPosition().toPoint()))
                    if edges != Qt.Edge(0):
                        handle = self.windowHandle()
                        if handle is not None:
                            try:
                                if handle.startSystemResize(edges):
                                    event.accept()
                                    return True
                            except Exception:
                                pass
            elif event_type in (QEvent.Type.Leave, QEvent.Type.WindowDeactivate):
                if not self.isMaximized():
                    self.unsetCursor()
        return super().eventFilter(watched, event)

    def resize_edges_at(self, pos: QPoint):
        if pos.x() < 0 or pos.y() < 0 or pos.x() > self.width() or pos.y() > self.height():
            return Qt.Edge(0)
        margin = self.resize_margin
        edges = Qt.Edge(0)
        if pos.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def update_resize_cursor(self, pos: QPoint):
        edges = self.resize_edges_at(pos)
        if edges == Qt.Edge(0):
            self.unsetCursor()
            return
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge) or (edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif horizontal:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif vertical:
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def update_personal_section_state(self):
        enabled = self.personal_enabled_check.isChecked()
        widgets = [
            self.member_name_edit,
            self.member_type_combo,
            self.member_authority_combo,
            self.personal_file_edit,
            self.personnel_model_file_edit,
            self.personal_level_combo,
            self.personal_department_combo,
            self.personal_position_combo,
            self.personal_notes_edit,
            self.has_peer_models_check,
            self.detect_department_btn,
            self.import_members_dir_btn,
            self.apply_detected_department_btn,
            self.mark_manual_department_btn,
            self.add_member_btn,
            self.new_member_btn,
            self.delete_member_btn,
            self.organization_members_list,
        ]
        for widget in widgets:
            widget.setEnabled(enabled)
        self.refresh_summary()

    def log_message(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        safe_message = str(redact_secrets(str(message)))
        line = f"[{timestamp}] {safe_message}"
        self.log_history.append(line)
        self.log_history = self.log_history[-300:]
        append_task_log(self.current_task_log_path, line)
        if hasattr(self, "logger"):
            try:
                if "❌" in safe_message or "失败" in safe_message or "异常" in safe_message:
                    self.logger.error(safe_message)
                else:
                    self.logger.info(safe_message)
            except Exception:
                pass
        if hasattr(self, "log_text"):
            self.log_text.append(line)
            self.log_text.ensureCursorVisible()
        if hasattr(self, "status_label"):
            if safe_message.startswith("📋 执行命令"):
                return
            brief = re.sub(r"^[✅🚀📋🔎✍️➕🗑⚠️❌💾🧾⏱️⏹️]+\s*", "", safe_message).strip()
            if len(brief) > 58:
                brief = brief[:55] + "..."
            self.status_label.setText(brief or "准备就绪")
        else:
            print(line)

    def restore_app_state(self):
        try:
            self.app_settings.restore_window_state(self)
            recent_output = self.app_settings.recent_output_dir()
            if recent_output and recent_output.exists() and hasattr(self, "result_group"):
                self.populate_result_viewer(recent_output)
                self.log_message(f"📁 已恢复最近输出目录：{recent_output}")
            last_project = self.app_settings.last_project()
            if last_project and last_project.exists():
                self.current_project_path = last_project
                self.update_project_label()
        except Exception as exc:
            self.log_message(f"⚠️ 恢复界面状态失败：{exc}")

    def save_app_state(self):
        try:
            self.app_settings.save_window_state(self)
            if self.current_output_dir:
                self.app_settings.save_recent_output_dir(self.current_output_dir)
            if self.current_project_path:
                self.app_settings.save_last_project(self.current_project_path)
        except Exception as exc:
            if hasattr(self, "logger"):
                self.logger.error("保存界面状态失败：%s", exc)

    def closeEvent(self, event):  # pragma: no cover - GUI behavior
        self.save_app_state()
        super().closeEvent(event)


def main():
    if PYSIDE_IMPORT_ERROR is not None:
        print("❌ PySide6 未安装，无法启动 Qt GUI。")
        print("请先执行：pip install -r requirements.txt")
        print(f"原始错误：{PYSIDE_IMPORT_ERROR}")
        legacy = Path(__file__).with_name("gui_legacy_tkinter.py")
        if legacy.exists():
            print("如需使用旧版界面，可运行：python gui_legacy_tkinter.py")
        return
    app = QApplication(sys.argv)
    window = ModernCompanySkillGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

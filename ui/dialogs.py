# -*- coding: utf-8 -*-
"""Themed dialogs for 算疏智合.

Keeping the custom dialog here avoids duplicating modal window styling and
prevents the main window from owning all dialog implementation details.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
)

from ui.theme import COLORS

class ThemeMessageDialog(QDialog):
    """Small custom modal dialog that follows the dark workstation visual language."""

    ICONS = {
        "error": "×",
        "warning": "!",
        "info": "i",
        "question": "?",
    }

    TITLES = {
        "error": "需要补充信息",
        "warning": "请确认",
        "info": "提示",
        "question": "请确认",
    }

    def __init__(self, parent: QWidget | None, kind: str, title: str, message: str, buttons: list[tuple[str, bool]] | None = None, subtitle: str | None = None):
        super().__init__(parent)
        self.kind = kind if kind in self.ICONS else "info"
        self.result_value = False
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setObjectName("ThemeDialog")
        self.setMinimumWidth(430)
        self.setMaximumWidth(520)
        self._build_ui(title or self.TITLES[self.kind], message, buttons or [("知道了", True)], subtitle)
        self.setStyleSheet(self._dialog_stylesheet())

    def _dialog_stylesheet(self) -> str:
        return f"""
            QDialog#ThemeDialog {{ background: transparent; }}
            QFrame#DialogShell {{ background: {COLORS['gradient_card']}; border: 1px solid {COLORS['line_strong']}; border-radius: 20px; }}
            QFrame#DialogTitleBar {{ background: transparent; border: none; }}
            QLabel#DialogTitle {{ color: {COLORS['text']}; font-size: 12.5pt; font-weight: 850; }}
            QLabel#DialogSubtitle {{ color: {COLORS['muted']}; font-size: 9pt; }}
            QLabel#DialogMessage {{ color: {COLORS['text_soft']}; font-size: 10.5pt; line-height: 150%; }}
            QLabel#DialogIcon {{ color: white; background: {COLORS['gradient_main']}; border: 1px solid {COLORS['primary_border_2']}; border-radius: 24px; font-size: 20pt; font-weight: 900; qproperty-alignment: AlignCenter; }}
            QLabel#DialogIcon[kind="error"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ff4d6d, stop:1 #8b5cf6); border-color: #7f1d1d; }}
            QLabel#DialogIcon[kind="warning"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #f59e0b, stop:1 #8b5cf6); border-color: #92400e; }}
            QLabel#DialogIcon[kind="question"] {{ background: {COLORS['gradient_main']}; border-color: {COLORS['primary_border_2']}; }}
            QPushButton#DialogPrimaryButton {{ color: white; background: {COLORS['gradient_main']}; border: 1px solid {COLORS['primary_border_2']}; border-radius: 12px; min-height: 34px; padding: 8px 18px; font-weight: 800; }}
            QPushButton#DialogPrimaryButton:hover {{ background: {COLORS['gradient_hover']}; border-color: {COLORS['primary_2']}; }}
            QPushButton#DialogSecondaryButton {{ color: {COLORS['text_soft']}; background: #171a24; border: 1px solid {COLORS['line_strong']}; border-radius: 12px; min-height: 34px; padding: 8px 18px; font-weight: 760; }}
            QPushButton#DialogSecondaryButton:hover {{ color: white; background: {COLORS['gradient_button_soft']}; border-color: {COLORS['primary_border']}; }}
            QPushButton#DialogCloseButton {{ color: {COLORS['muted']}; background: transparent; border: none; border-radius: 10px; min-height: 26px; padding: 0; font-size: 14pt; font-weight: 800; }}
            QPushButton#DialogCloseButton:hover {{ color: white; background: rgba(255,255,255,0.08); }}
        """

    def _build_ui(self, title: str, message: str, buttons: list[tuple[str, bool]], subtitle: str | None = None):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("DialogShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(20, 16, 20, 18)
        shell_layout.setSpacing(16)
        outer.addWidget(shell)

        title_bar = QFrame()
        title_bar.setObjectName("DialogTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("DialogTitle")
        title_box.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("DialogSubtitle")
            title_box.addWidget(subtitle_label)
        title_layout.addLayout(title_box, 1)

        close_btn = QPushButton("×")
        close_btn.setObjectName("DialogCloseButton")
        close_btn.setFixedSize(30, 28)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.clicked.connect(self.reject)
        title_layout.addWidget(close_btn)
        shell_layout.addWidget(title_bar)

        body = QHBoxLayout()
        body.setSpacing(16)
        icon = QLabel(self.ICONS[self.kind])
        icon.setObjectName("DialogIcon")
        icon.setProperty("kind", self.kind)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(50, 50)
        body.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        msg = QLabel(message)
        msg.setObjectName("DialogMessage")
        msg.setWordWrap(True)
        msg.setMinimumWidth(280)
        body.addWidget(msg, 1)
        shell_layout.addLayout(body)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        for text, accepted in buttons:
            btn = QPushButton(text)
            btn.setObjectName("DialogPrimaryButton" if accepted else "DialogSecondaryButton")
            btn.clicked.connect(lambda checked=False, ok=accepted: self._finish(ok))
            btn_row.addWidget(btn)
        shell_layout.addLayout(btn_row)

        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0, 0, 0, 150))
        effect.setBlurRadius(36)
        effect.setOffset(0, 12)
        shell.setGraphicsEffect(effect)

    def _finish(self, accepted: bool):
        self.result_value = accepted
        if accepted:
            self.accept()
        else:
            self.reject()

    @classmethod
    def show(cls, parent: QWidget | None, kind: str, title: str, message: str, buttons: list[tuple[str, bool]] | None = None, subtitle: str | None = None) -> bool:
        dialog = cls(parent, kind, title, message, buttons, subtitle)
        dialog.exec()
        return bool(dialog.result_value)

    @classmethod
    def open_non_blocking(cls, parent: QWidget | None, kind: str, title: str, message: str, buttons: list[tuple[str, bool]] | None = None, subtitle: str | None = None):
        """Open a dialog without starting a nested event loop.

        Model connection check results are emitted from a worker thread. Showing a modal
        dialog with exec() while the worker thread is finishing can create a nested event
        loop and process deleteLater events at an unsafe time. open() keeps the main Qt
        event loop in control; the caller must keep a Python reference until the dialog
        finishes.
        """
        dialog = cls(parent, kind, title, message, buttons, subtitle)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.open()
        return dialog




class TemplateEditorDialog(QDialog):
    """Dark themed editor for custom project templates."""

    def __init__(self, parent: QWidget | None, template_data: dict | None = None, title: str = "编辑模板"):
        super().__init__(parent)
        self.result_value = False
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setObjectName("TemplateEditorDialog")
        self.setMinimumWidth(760)
        self.setMinimumHeight(700)
        self.template_data = template_data or {}
        self._build_ui(title)
        self.setStyleSheet(self._stylesheet())
        self._load_data(self.template_data)

    def _stylesheet(self) -> str:
        return f"""
            QDialog#TemplateEditorDialog {{ background: transparent; }}
            QFrame#TemplateEditorShell {{ background: {COLORS['gradient_card']}; border: 1px solid {COLORS['line_strong']}; border-radius: 20px; }}
            QLabel#TemplateEditorTitle {{ color: {COLORS['text']}; font-size: 13pt; font-weight: 850; }}
            QLabel#TemplateEditorHint {{ color: {COLORS['muted']}; font-size: 9pt; }}
            QLabel {{ color: {COLORS['text_soft']}; }}
            QLineEdit, QTextEdit {{ color: {COLORS['text']}; background: #111522; border: 1px solid {COLORS['line_strong']}; border-radius: 12px; padding: 8px 10px; selection-background-color: {COLORS['primary_border']}; }}
            QLineEdit:focus, QTextEdit:focus {{ border-color: {COLORS['primary_border_2']}; background: #131827; }}
            QPushButton#TemplatePrimaryButton {{ color: white; background: {COLORS['gradient_main']}; border: 1px solid {COLORS['primary_border_2']}; border-radius: 12px; min-height: 34px; padding: 8px 18px; font-weight: 800; }}
            QPushButton#TemplatePrimaryButton:hover {{ background: {COLORS['gradient_hover']}; }}
            QPushButton#TemplateSecondaryButton {{ color: {COLORS['text_soft']}; background: #171a24; border: 1px solid {COLORS['line_strong']}; border-radius: 12px; min-height: 34px; padding: 8px 18px; font-weight: 760; }}
            QPushButton#TemplateSecondaryButton:hover {{ color: white; background: {COLORS['gradient_button_soft']}; border-color: {COLORS['primary_border']}; }}
            QPushButton#TemplateCloseButton {{ color: {COLORS['muted']}; background: transparent; border: none; border-radius: 10px; min-height: 26px; padding: 0; font-size: 14pt; font-weight: 800; }}
            QPushButton#TemplateCloseButton:hover {{ color: white; background: rgba(255,255,255,0.08); }}
        """

    def _build_ui(self, title: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        shell.setObjectName("TemplateEditorShell")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(14)
        outer.addWidget(shell)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("TemplateEditorTitle")
        hint = QLabel("可编辑模板名称、说明、部门、成员建议和生成偏好。内置模板不会被覆盖，保存后会成为自定义模板。")
        hint.setObjectName("TemplateEditorHint")
        hint.setWordWrap(True)
        title_box.addWidget(title_label)
        title_box.addWidget(hint)
        title_row.addLayout(title_box, 1)
        close_btn = QPushButton("×")
        close_btn.setObjectName("TemplateCloseButton")
        close_btn.setFixedSize(30, 28)
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：医疗科技公司模板")
        self.description_edit = QTextEdit()
        self.description_edit.setFixedHeight(58)
        self.description_edit.setPlaceholderText("模板适用场景说明。")
        self.selected_departments_edit = QTextEdit()
        self.selected_departments_edit.setFixedHeight(82)
        self.selected_departments_edit.setPlaceholderText("每行一个标准部门，或用顿号 / 逗号分隔。")
        self.custom_departments_edit = QTextEdit()
        self.custom_departments_edit.setFixedHeight(82)
        self.custom_departments_edit.setPlaceholderText("每行一个自定义部门。")
        self.org_notes_edit = QTextEdit()
        self.org_notes_edit.setFixedHeight(76)
        self.org_notes_edit.setPlaceholderText("组织架构补充说明。")
        self.confirmation_notes_edit = QTextEdit()
        self.confirmation_notes_edit.setFixedHeight(76)
        self.confirmation_notes_edit.setPlaceholderText("生成偏好，会写入最终补充说明。")
        self.members_edit = QTextEdit()
        self.members_edit.setFixedHeight(92)
        self.members_edit.setPlaceholderText("每行：姓名｜层级｜部门｜职责，例如：CEO｜CEO/总裁层｜董事会/创始办公室｜战略方向")

        grid.addWidget(QLabel("模板名称"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(QLabel("模板说明"), 1, 0)
        grid.addWidget(self.description_edit, 1, 1)
        grid.addWidget(QLabel("建议启用部门"), 2, 0)
        grid.addWidget(self.selected_departments_edit, 2, 1)
        grid.addWidget(QLabel("建议新增部门"), 3, 0)
        grid.addWidget(self.custom_departments_edit, 3, 1)
        grid.addWidget(QLabel("组织备注"), 4, 0)
        grid.addWidget(self.org_notes_edit, 4, 1)
        grid.addWidget(QLabel("生成偏好"), 5, 0)
        grid.addWidget(self.confirmation_notes_edit, 5, 1)
        grid.addWidget(QLabel("建议插槽成员"), 6, 0)
        grid.addWidget(self.members_edit, 6, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("TemplateSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存模板")
        save_btn.setObjectName("TemplatePrimaryButton")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0, 0, 0, 160))
        effect.setBlurRadius(40)
        effect.setOffset(0, 14)
        shell.setGraphicsEffect(effect)

    def _load_data(self, data: dict):
        self.name_edit.setText(str(data.get("name") or ""))
        self.description_edit.setPlainText(str(data.get("description") or ""))
        self.selected_departments_edit.setPlainText("\n".join(str(item) for item in data.get("selected_departments", []) or []))
        self.custom_departments_edit.setPlainText("\n".join(str(item) for item in data.get("custom_departments", []) or []))
        self.org_notes_edit.setPlainText(str(data.get("org_notes") or ""))
        self.confirmation_notes_edit.setPlainText(str(data.get("confirmation_notes") or ""))
        members = []
        for member in data.get("recommended_members", []) or []:
            if isinstance(member, dict):
                members.append("｜".join([
                    str(member.get("name") or ""),
                    str(member.get("level") or ""),
                    str(member.get("department") or ""),
                    str(member.get("position") or ""),
                ]))
        self.members_edit.setPlainText("\n".join(members))

    @staticmethod
    def _split_lines(text: str) -> list[str]:
        import re
        return [part.strip() for part in re.split(r"[\n,，、;；]+", text or "") if part.strip()]

    @staticmethod
    def _parse_members(text: str) -> list[dict]:
        import re
        members = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in re.split(r"[|｜]", line)]
            while len(parts) < 4:
                parts.append("")
            if parts[0]:
                members.append({"name": parts[0], "level": parts[1], "department": parts[2], "position": parts[3]})
        return members

    def get_template_data(self) -> dict:
        name = self.name_edit.text().strip()
        return {
            "name": name,
            "description": self.description_edit.toPlainText().strip(),
            "selected_departments": self._split_lines(self.selected_departments_edit.toPlainText()),
            "custom_departments": self._split_lines(self.custom_departments_edit.toPlainText()),
            "org_notes": self.org_notes_edit.toPlainText().strip(),
            "confirmation_notes": self.confirmation_notes_edit.toPlainText().strip(),
            "recommended_members": self._parse_members(self.members_edit.toPlainText()),
        }

    @classmethod
    def edit(cls, parent: QWidget | None, template_data: dict | None = None, title: str = "编辑模板") -> tuple[bool, dict]:
        dialog = cls(parent, template_data, title)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return True, dialog.get_template_data()
        return False, {}

# -*- coding: utf-8 -*-
"""Shared visual theme for 算疏智合 Qt widgets.

Centralizes color tokens, global QSS, and soft shadow helpers so
visual changes no longer require editing the large main-window module.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget

COLORS = {
    # Hardened model connection check with timeout and cancellation.
    "bg": "#101117",
    "shell": "#12131b",
    "panel": "#181a24",
    "panel_soft": "#202230",
    "panel_hover": "#272a3a",
    "panel_glass": "rgba(28,30,42,0.78)",
    "text": "#f8fafc",
    "text_soft": "#e5e7eb",
    "muted": "#a4acbb",
    "muted_soft": "#6b7280",
    "line": "#2d3142",
    "line_soft": "#242838",
    "line_strong": "#3b4260",
    "primary": "#3b82f6",
    "primary_mid": "#6366f1",
    "primary_2": "#8b5cf6",
    "primary_3": "#a855f7",
    "primary_dark": "#c7d2fe",
    "primary_soft": "#1d2341",
    "primary_soft_2": "#261f40",
    "primary_border": "#4854a3",
    "primary_border_2": "#5b4fb5",
    "accent": "#8b5cf6",
    "green": "#22c55e",
    "green_soft": "#11271c",
    "amber": "#f59e0b",
    "red": "#ff4d6d",
    "red_soft": "#331923",
    "log_bg": "#0d0f15",
    "log_fg": "#dbeafe",
    "shadow_rgba": (0, 0, 0, 95),
    "gradient_main": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f6df6, stop:0.52 #635bff, stop:1 #8b5cf6)",
    "gradient_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:0.55 #4f46e5, stop:1 #7c3aed)",
    "gradient_soft": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #202538, stop:0.52 #1a1c29, stop:1 #241a35)",
    "gradient_soft_2": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #141620, stop:0.54 #12131b, stop:1 #171323)",
    "gradient_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0e111a, stop:0.45 #131421, stop:1 #1a1226)",
    "gradient_card": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1d2030, stop:0.58 #181a24, stop:1 #21182f)",
    "gradient_badge": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #24345e, stop:1 #302459)",
    "gradient_title": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #181b28, stop:0.48 #151722, stop:1 #1f1630)",
    "gradient_button_soft": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #202943, stop:1 #2a2144)",
    "gradient_sidebar_active": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f6df6, stop:1 #8b5cf6)",
    "gradient_search": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #191d2c, stop:1 #2a1c35)",
}


def build_app_stylesheet() -> str:
    return f"""
            QMainWindow {{ background: {COLORS['gradient_bg']}; }}
            QWidget {{ color: {COLORS['text']}; font-family: "Microsoft YaHei"; font-size: 10pt; }}
            QWidget#AppShell {{ background: {COLORS['gradient_soft_2']}; border: 1px solid {COLORS['line']}; border-radius: 22px; }}
            QStackedWidget#StepStack {{ background: transparent; border: none; }}

            QFrame#TitleBar {{ background: {COLORS['gradient_title']}; border: 1px solid {COLORS['line_soft']}; border-radius: 18px; }}
            QLabel#TitleMark {{ color: #ff4d6d; font-size: 16pt; font-weight: 900; }}
            QLabel#TitleBarText {{ color: {COLORS['text_soft']}; font-size: 10pt; font-weight: 720; }}
            QPushButton#WindowButton {{ color: {COLORS['muted']}; background: transparent; border: none; border-radius: 10px; padding: 0; font-size: 11pt; font-weight: 800; }}
            QPushButton#WindowButton:hover {{ color: white; background: rgba(255,255,255,0.08); border: none; }}
            QPushButton#DialogCloseButton {{ color: {COLORS['muted']}; background: transparent; border: none; border-radius: 10px; padding: 0; font-size: 14pt; font-weight: 800; min-height: 26px; }}
            QPushButton#DialogCloseButton:hover {{ color: white; background: rgba(255,255,255,0.08); border: none; }}
            QPushButton#CloseButton {{ color: {COLORS['muted']}; background: transparent; border: none; border-radius: 10px; padding: 0; font-size: 15pt; font-weight: 800; }}
            QPushButton#CloseButton:hover {{ color: white; background: {COLORS['red']}; border: none; }}

            QFrame#Sidebar, QFrame#Card, QGroupBox {{ background: {COLORS['gradient_card']}; border: 1px solid {COLORS['line_soft']}; border-radius: 18px; }}
            QFrame#Sidebar {{ background: #151720; border-color: #242838; }}
            QFrame#ActionBar {{ background: transparent; border: none; border-radius: 0px; }}
            QLabel#BrandTitle {{ color: {COLORS['text']}; font-size: 19pt; font-weight: 850; letter-spacing: -0.3px; }}
            QLabel#BrandSubtitle, QLabel#PageSubtitle, QLabel#SmallHint {{ color: {COLORS['muted']}; font-size: 9.5pt; }}
            QLabel#SidebarSection {{ color: {COLORS['muted_soft']}; font-size: 8.5pt; font-weight: 800; padding-top: 8px; }}
            QLabel#PageTitle {{ color: {COLORS['text']}; font-size: 17pt; font-weight: 850; letter-spacing: -0.4px; }}
            QWidget#PageCanvas {{ background: transparent; }}
            QWidget#ScrollViewport {{ background: transparent; }}

            QGroupBox {{ margin-top: 18px; padding: 20px 16px 16px 16px; font-weight: 760; color: {COLORS['text_soft']}; }}
            QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; padding: 0px 6px; margin-left: 16px; color: {COLORS['primary_dark']}; background: transparent; border: none; }}

            QPushButton {{ min-height: 36px; background: #171a24; border: 1px solid {COLORS['line']}; border-radius: 12px; padding: 9px 15px; color: {COLORS['text_soft']}; font-weight: 680; }}
            QPushButton:hover {{ background: {COLORS['gradient_button_soft']}; border-color: {COLORS['primary_border']}; color: white; }}
            QPushButton:pressed {{ background: #292142; }}
            QPushButton:disabled {{ color: {COLORS['muted_soft']}; background: #151822; border-color: #222633; }}
            QPushButton[variant="primary"] {{ color: white; background: {COLORS['gradient_main']}; border-color: {COLORS['primary_border_2']}; }}
            QPushButton[variant="primary"]:hover {{ color: white; background: {COLORS['gradient_hover']}; border-color: {COLORS['primary_2']}; }}
            QPushButton[variant="accent"] {{ color: {COLORS['primary_dark']}; background: {COLORS['gradient_button_soft']}; border-color: {COLORS['primary_border']}; }}
            QPushButton[variant="accent"]:hover {{ color: white; background: {COLORS['gradient_main']}; border-color: {COLORS['primary_mid']}; }}
            QPushButton[variant="danger"] {{ color: #fecdd3; background: {COLORS['red_soft']}; border-color: #7f1d1d; }}
            QPushButton[variant="danger"]:hover {{ color: white; background: {COLORS['red']}; border-color: {COLORS['red']}; }}
            QPushButton[step="current"] {{ color: white; background: {COLORS['gradient_sidebar_active']}; border: 1px solid transparent; text-align: left; padding-left: 16px; font-weight: 820; }}
            QPushButton[step="done"] {{ color: {COLORS['text_soft']}; background: #1b2234; border: 1px solid {COLORS['line_strong']}; text-align: left; padding-left: 16px; }}
            QPushButton[step="pending"] {{ color: {COLORS['muted']}; background: transparent; border: 1px solid transparent; text-align: left; padding-left: 16px; }}
            QPushButton[step="pending"]:hover {{ color: {COLORS['text']}; background: #1a1d2a; border-color: {COLORS['line']}; }}

            QLineEdit, QComboBox, QTextEdit, QListWidget {{ background: #11141d; border: 1px solid {COLORS['line']}; border-radius: 12px; padding: 8px 10px; color: {COLORS['text_soft']}; selection-background-color: #4338ca; selection-color: white; }}
            QLineEdit, QComboBox {{ min-height: 36px; }}
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QListWidget:focus {{ border-color: {COLORS['primary_mid']}; background: #151927; }}
            QComboBox::drop-down {{ width: 30px; border: none; border-left: 1px solid {COLORS['line_soft']}; border-top-right-radius: 12px; border-bottom-right-radius: 12px; background: transparent; }}
            QComboBox::down-arrow {{ width: 0px; height: 0px; }}
            QComboBox QAbstractItemView {{ background: #171a24; border: 1px solid {COLORS['line_strong']}; border-radius: 10px; selection-background-color: #3730a3; selection-color: white; padding: 4px; }}
            QListWidget::item {{ padding: 9px; margin: 3px; border-radius: 10px; }}
            QListWidget::item:hover {{ background: {COLORS['panel_hover']}; }}
            QListWidget::item:selected {{ background: {COLORS['gradient_badge']}; color: white; }}
            QTextEdit#LogText {{ background: #0d0f15; color: {COLORS['log_fg']}; border: 1px solid {COLORS['line']}; border-radius: 16px; font-family: Consolas, "Microsoft YaHei"; font-size: 9.5pt; }}

            QCheckBox, QRadioButton {{ spacing: 8px; color: {COLORS['text_soft']}; }}
            QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; border: 1px solid {COLORS['line_strong']}; background: #10131c; }}
            QCheckBox::indicator {{ border-radius: 5px; }}
            QRadioButton::indicator {{ border-radius: 9px; }}
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {COLORS['gradient_main']}; border: 1px solid {COLORS['primary_mid']}; }}
            QProgressBar#TaskProgress {{ background: #11141d; border: 1px solid {COLORS['line']}; border-radius: 8px; min-height: 12px; max-height: 12px; text-align: center; color: transparent; }}
            QProgressBar#TaskProgress::chunk {{ background: {COLORS['gradient_main']}; border-radius: 8px; }}
            QLabel#TaskProgressLabel {{ color: {COLORS['muted']}; font-size: 9pt; font-weight: 680; }}
            QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: #30364a; border-radius: 5px; min-height: 34px; }}
            QScrollBar::handle:vertical:hover {{ background: {COLORS['primary_mid']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

        """


def apply_app_theme(target: QWidget) -> None:
    """Apply the 算疏智合 dark workstation visual system."""
    QApplication.setFont(QFont("Microsoft YaHei", 10))
    app = QApplication.instance()
    if app is not None:
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
    target.setStyleSheet(build_app_stylesheet())


def apply_soft_shadow(widget: QWidget, blur_radius: int = 28, y_offset: int = 8) -> None:
    """Add a subtle unified shadow to top-level visual surfaces."""
    effect = QGraphicsDropShadowEffect(widget)
    r, g, b, a = COLORS["shadow_rgba"]
    effect.setColor(QColor(r, g, b, a))
    effect.setBlurRadius(blur_radius)
    effect.setOffset(0, y_offset)
    widget.setGraphicsEffect(effect)

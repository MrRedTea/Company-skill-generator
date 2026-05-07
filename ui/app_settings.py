"""Persistent GUI settings for 算疏智合."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings


class AppSettings:
    """Thin wrapper around QSettings for durable UI preferences."""

    def __init__(self, app_name: str = "算疏智合"):
        self.settings = QSettings("SkillPlatform", app_name)

    def restore_window_state(self, window) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            try:
                window.restoreGeometry(geometry)
            except Exception:
                pass

    def save_window_state(self, window) -> None:
        try:
            self.settings.setValue("window/geometry", window.saveGeometry())
            self.settings.setValue("window/is_maximized", window.isMaximized())
        except Exception:
            pass

    def save_recent_output_dir(self, path: str | Path | None) -> None:
        if not path:
            return
        self.settings.setValue("recent/output_dir", str(path))

    def recent_output_dir(self) -> Path | None:
        value = self.settings.value("recent/output_dir", "")
        text = str(value or "").strip()
        return Path(text) if text else None

    def save_recent_project(self, path: str | Path | None, limit: int = 8) -> None:
        if not path:
            return
        text = str(Path(path))
        projects = [item for item in self.recent_projects() if item != text]
        projects.insert(0, text)
        self.settings.setValue("recent/projects", projects[:limit])

    def recent_projects(self) -> list[str]:
        value = self.settings.value("recent/projects", [])
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
        else:
            items = []
        return [item for item in items if item.strip()]

    def save_last_project(self, path: str | Path | None) -> None:
        if path:
            self.settings.setValue("recent/last_project", str(path))

    def last_project(self) -> Path | None:
        value = self.settings.value("recent/last_project", "")
        text = str(value or "").strip()
        return Path(text) if text else None

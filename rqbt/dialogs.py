from __future__ import annotations

import ntpath
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from .api import QbtClient
from .common import (
    APP_NAME, C_GREEN, C_MUTED, C_RED, DEFAULT_REFRESH_MS, DEFAULT_SAVE_PATH,
    DEFAULT_SERVER, DEFAULT_SMB_PATH, human_bytes,
)
from .config import save_config


class RemotePathDialog(QDialog):
    """Browse directories on the remote host using qBittorrent's own Web API."""

    def __init__(self, client: QbtClient, start_path: str, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Select folder on remote host")
        self.resize(680, 470)
        self.selected_path = start_path or self.client.default_save_path() or DEFAULT_SAVE_PATH

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(self.selected_path)
        self.up_btn = QPushButton("Up")
        self.refresh_btn = QPushButton("Refresh")
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.up_btn)
        row.addWidget(self.refresh_btn)
        root.addLayout(row)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        root.addWidget(self.list, 1)

        self.free_label = QLabel("")
        self.free_label.setStyleSheet(f"color:{C_MUTED};")
        root.addWidget(self.free_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Select Folder")
        root.addWidget(buttons)

        self.up_btn.clicked.connect(self.go_up)
        self.refresh_btn.clicked.connect(self.load)
        self.path_edit.returnPressed.connect(self.load)
        self.list.itemDoubleClicked.connect(self.enter_item)
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)
        self.load()

    def load(self):
        path = self.path_edit.text().strip()
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            names = self.client.directory_content(path, mode="dirs", with_metadata=False)
            self.list.clear()
            for item in sorted((str(x) for x in names), key=str.casefold):
                QListWidgetItem(item, self.list)
            self.selected_path = path
            free = self.client.free_space(path)
            self.free_label.setText(f"Free space: {human_bytes(free)}" if free >= 0 else "")
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Could not browse that folder on the remote host:\n\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    def go_up(self):
        current = self.path_edit.text().strip().rstrip("\\/")
        parent = ntpath.dirname(current)
        if parent and parent != current:
            if len(parent) == 2 and parent[1] == ":":
                parent += "\\"
            self.path_edit.setText(parent)
            self.load()

    def enter_item(self, item: QListWidgetItem):
        current = self.path_edit.text().strip()
        child = ntpath.join(current, item.text())
        self.path_edit.setText(child)
        self.load()

    def accept_selected(self):
        path = self.path_edit.text().strip()
        if not path:
            return
        # Validate using the remote API before returning it.
        try:
            self.client.directory_content(path, mode="dirs", with_metadata=False)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"That path is not accessible to qBittorrent on the remote host:\n\n{e}")
            return
        self.selected_path = path
        self.accept()


class AddTorrentDialog(QDialog):
    def __init__(
        self,
        client: QbtClient,
        sources: list[str],
        default_save_path: str,
        categories: list[str],
        tags: list[str],
        parent=None,
        prefs: dict[str, Any] | None = None,
    ):
        super().__init__(parent)
        self.client = client
        self.prefs = dict(prefs or {})
        self.setWindowTitle("Add New Torrent")
        self.resize(760, 610)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # Basic page ---------------------------------------------------------
        basic = QWidget()
        bl = QVBoxLayout(basic)
        bl.addWidget(QLabel("Torrent source(s):"))
        self.sources = QListWidget()
        self.sources.setAlternatingRowColors(True)
        for src in sources:
            self.sources.addItem(src)
        bl.addWidget(self.sources, 1)

        source_buttons = QHBoxLayout()
        self.add_file_btn = QPushButton("Add .torrent File…")
        self.add_link_btn = QPushButton("Add Link…")
        self.remove_source_btn = QPushButton("Remove")
        source_buttons.addWidget(self.add_file_btn)
        source_buttons.addWidget(self.add_link_btn)
        source_buttons.addWidget(self.remove_source_btn)
        source_buttons.addStretch()
        bl.addLayout(source_buttons)

        form = QFormLayout()
        path_row = QHBoxLayout()
        self.save_path = QLineEdit(default_save_path or DEFAULT_SAVE_PATH)
        self.browse_remote = QPushButton("Browse remote host…")
        path_row.addWidget(self.save_path, 1)
        path_row.addWidget(self.browse_remote)
        path_wrap = QWidget(); path_wrap.setLayout(path_row)
        form.addRow("Save at:", path_wrap)

        self.rename = QLineEdit()
        self.rename.setPlaceholderText("Optional — leave blank to use torrent name")
        form.addRow("Rename torrent:", self.rename)

        self.category = QComboBox(); self.category.setEditable(True)
        self.category.addItem("")
        self.category.addItems(sorted(categories, key=str.casefold))
        form.addRow("Category:", self.category)

        self.tags = QLineEdit()
        self.tags.setPlaceholderText("Comma-separated tags")
        form.addRow("Tags:", self.tags)
        bl.addLayout(form)

        flags = QGridLayout()
        self.start_now = QCheckBox("Start torrent")
        self.start_now.setChecked(not bool(self.prefs.get("add_stopped_enabled", False)))
        self.top_queue = QCheckBox("Add to top of queue")
        self.top_queue.setChecked(bool(self.prefs.get("add_to_top_of_queue", False)))
        self.force_start = QCheckBox("Force Start")
        self.sequential = QCheckBox("Download in sequential order")
        self.first_last = QCheckBox("Download first and last pieces first")
        self.auto_tmm = QCheckBox("Automatic Torrent Management")
        self.auto_tmm.setChecked(bool(self.prefs.get("auto_tmm_enabled", False)))
        flags.addWidget(self.start_now, 0, 0)
        flags.addWidget(self.top_queue, 0, 1)
        flags.addWidget(self.force_start, 1, 0)
        flags.addWidget(self.auto_tmm, 1, 1)
        flags.addWidget(self.sequential, 2, 0)
        flags.addWidget(self.first_last, 2, 1)
        bl.addLayout(flags)
        self.tabs.addTab(basic, "Basic")

        # Advanced page ------------------------------------------------------
        adv = QWidget()
        af = QFormLayout(adv)
        self.content_layout = QComboBox()
        self.content_layout.addItem("Use qBittorrent default", "")
        self.content_layout.addItem("Original", "Original")
        self.content_layout.addItem("Create subfolder", "Subfolder")
        self.content_layout.addItem("Do not create subfolder", "NoSubfolder")
        pref_layout = str(self.prefs.get("torrent_content_layout", "") or "")
        pref_idx = self.content_layout.findData(pref_layout)
        if pref_idx >= 0:
            self.content_layout.setCurrentIndex(pref_idx)
        af.addRow("Content layout:", self.content_layout)

        self.stop_condition = QComboBox()
        self.stop_condition.addItem("None", "None")
        self.stop_condition.addItem("Metadata received", "MetadataReceived")
        self.stop_condition.addItem("Files checked", "FilesChecked")
        pref_stop = str(self.prefs.get("torrent_stop_condition", "None") or "None")
        stop_idx = self.stop_condition.findData(pref_stop)
        if stop_idx >= 0:
            self.stop_condition.setCurrentIndex(stop_idx)
        af.addRow("Stop condition:", self.stop_condition)

        self.download_path_enabled = QCheckBox("Use incomplete/download path")
        self.download_path_enabled.setChecked(bool(self.prefs.get("temp_path_enabled", False)))
        download_path_row = QHBoxLayout()
        self.download_path = QLineEdit(str(self.prefs.get("temp_path", "") or ""))
        self.download_path_browse = QPushButton("Browse remote host…")
        download_path_row.addWidget(self.download_path, 1)
        download_path_row.addWidget(self.download_path_browse)
        download_wrap = QWidget(); download_wrap.setLayout(download_path_row)
        af.addRow(self.download_path_enabled, download_wrap)

        self.up_limit = QSpinBox(); self.up_limit.setRange(0, 10_000_000); self.up_limit.setSuffix(" KiB/s"); self.up_limit.setSpecialValueText("Unlimited")
        self.dl_limit = QSpinBox(); self.dl_limit.setRange(0, 10_000_000); self.dl_limit.setSuffix(" KiB/s"); self.dl_limit.setSpecialValueText("Unlimited")
        af.addRow("Upload limit:", self.up_limit)
        af.addRow("Download limit:", self.dl_limit)

        self.ratio_mode = QComboBox()
        self.ratio_mode.addItem("Use global settings", -2.0)
        self.ratio_mode.addItem("Unlimited", -1.0)
        self.ratio_mode.addItem("Custom…", 0.0)
        self.ratio_custom = QDoubleSpinBox(); self.ratio_custom.setRange(0.0, 10000.0); self.ratio_custom.setDecimals(2); self.ratio_custom.setValue(2.0)
        ratio_row = QHBoxLayout(); ratio_row.addWidget(self.ratio_mode); ratio_row.addWidget(self.ratio_custom)
        ratio_wrap = QWidget(); ratio_wrap.setLayout(ratio_row)
        af.addRow("Ratio limit:", ratio_wrap)

        self.seed_time_mode = QComboBox()
        self.seed_time_mode.addItem("Use global settings", -2)
        self.seed_time_mode.addItem("Unlimited", -1)
        self.seed_time_mode.addItem("Custom…", 0)
        self.seed_time_custom = QSpinBox(); self.seed_time_custom.setRange(0, 10_000_000); self.seed_time_custom.setSuffix(" min")
        strow = QHBoxLayout(); strow.addWidget(self.seed_time_mode); strow.addWidget(self.seed_time_custom)
        stwrap = QWidget(); stwrap.setLayout(strow)
        af.addRow("Seeding time limit:", stwrap)

        self.share_action = QComboBox()
        for label, data in [
            ("Use global action", "Default"),
            ("Stop torrent", "Stop"),
            ("Remove torrent", "Remove"),
            ("Remove torrent and files", "RemoveWithContent"),
            ("Enable Super Seeding", "EnableSuperSeeding"),
        ]:
            self.share_action.addItem(label, data)
        af.addRow("When share limit reached:", self.share_action)

        self.seed_mode = QCheckBox("Skip hash check / seed mode (files already complete)")
        af.addRow("", self.seed_mode)
        self.tabs.addTab(adv, "Advanced")

        hint = QLabel("The selected path is native to the remote qBittorrent host. The torrent engine, storage, and seeding stay there.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{C_MUTED};")
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Download")
        root.addWidget(buttons)

        self.add_file_btn.clicked.connect(self.add_files)
        self.add_link_btn.clicked.connect(self.add_link)
        self.remove_source_btn.clicked.connect(self.remove_source)
        self.browse_remote.clicked.connect(lambda: self.pick_remote(self.save_path))
        self.download_path_browse.clicked.connect(lambda: self.pick_remote(self.download_path))
        buttons.accepted.connect(self.validate_accept)
        buttons.rejected.connect(self.reject)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add Torrent Files", str(Path.home() / "Downloads"), "Torrent files (*.torrent);;All files (*.*)")
        for f in files:
            self.sources.addItem(f)

    def add_link(self):
        text, ok = _multiline_text_dialog(self, "Add Torrent Link", "Paste magnet link(s) or direct .torrent URL(s):")
        if ok:
            from .api import QbtClient
            for src in QbtClient.parse_sources(text):
                self.sources.addItem(src)

    def remove_source(self):
        for item in self.sources.selectedItems():
            self.sources.takeItem(self.sources.row(item))

    def pick_remote(self, target: QLineEdit):
        start = target.text().strip() or self.save_path.text().strip() or DEFAULT_SAVE_PATH
        dialog = RemotePathDialog(self.client, start, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            target.setText(dialog.selected_path)

    def source_list(self) -> list[str]:
        return [self.sources.item(i).text() for i in range(self.sources.count())]

    def _ratio(self) -> float:
        mode = float(self.ratio_mode.currentData())
        return self.ratio_custom.value() if mode == 0.0 else mode

    def _seed_time(self) -> int:
        mode = int(self.seed_time_mode.currentData())
        return self.seed_time_custom.value() if mode == 0 else mode

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "savepath": self.save_path.text().strip(),
            "rename": self.rename.text().strip(),
            "category": self.category.currentText().strip(),
            "tags": self.tags.text().strip(),
            "stopped": not self.start_now.isChecked(),
            "addToTopOfQueue": self.top_queue.isChecked(),
            "forced": self.force_start.isChecked(),
            "sequentialDownload": self.sequential.isChecked(),
            "firstLastPiecePrio": self.first_last.isChecked(),
            "autoTMM": self.auto_tmm.isChecked(),
            # 5.2.1 calls this skip_checking; newer API branches call it seedMode.
            # Send both so one UI works across the 5.2.x family.
            "skip_checking": self.seed_mode.isChecked(),
            "seedMode": self.seed_mode.isChecked(),
            "upLimit": -1 if self.up_limit.value() == 0 else self.up_limit.value() * 1024,
            "dlLimit": -1 if self.dl_limit.value() == 0 else self.dl_limit.value() * 1024,
            "ratioLimit": self._ratio(),
            "seedingTimeLimit": self._seed_time(),
            "inactiveSeedingTimeLimit": -2,
            "shareLimitAction": self.share_action.currentData(),
            "stopCondition": self.stop_condition.currentData(),
        }
        layout = self.content_layout.currentData()
        if layout:
            options["contentLayout"] = layout
        if self.download_path_enabled.isChecked():
            options["useDownloadPath"] = True
            options["downloadPath"] = self.download_path.text().strip()
        return options

    def validate_accept(self):
        if not self.source_list():
            QMessageBox.warning(self, APP_NAME, "Add at least one torrent source.")
            return
        if not self.save_path.text().strip():
            QMessageBox.warning(self, APP_NAME, "Choose a save location on the remote qBittorrent host.")
            return
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], parent=None):
        super().__init__(parent)
        self.cfg = cfg.copy()
        self.result_cfg: dict[str, Any] | None = None
        self.setWindowTitle("Options — RemoteQBT")
        self.resize(650, 480)
        root = QVBoxLayout(self)

        tabs = QTabWidget(); root.addWidget(tabs, 1)
        conn = QWidget(); form = QFormLayout(conn)
        self.server = QLineEdit(str(cfg.get("server", DEFAULT_SERVER)))
        self.key = QLineEdit(str(cfg.get("api_key", ""))); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_path = QLineEdit(str(cfg.get("save_path", DEFAULT_SAVE_PATH)))
        self.smb_path = QLineEdit(str(cfg.get("smb_path", DEFAULT_SMB_PATH)))
        self.refresh = QComboBox()
        for label, value in [("1 second", 1000), ("1.5 seconds", 1500), ("2 seconds", 2000), ("5 seconds", 5000), ("10 seconds", 10000)]:
            self.refresh.addItem(label, value)
        idx = self.refresh.findData(int(cfg.get("refresh_ms", DEFAULT_REFRESH_MS)))
        self.refresh.setCurrentIndex(max(0, idx))
        form.addRow("qBittorrent URL:", self.server)
        form.addRow("API key:", self.key)
        form.addRow("Default remote save path:", self.save_path)
        form.addRow("Laptop/SMB mirror path:", self.smb_path)
        form.addRow("Refresh interval:", self.refresh)
        tabs.addTab(conn, "Connection")

        ui = QWidget(); uil = QVBoxLayout(ui)
        self.live_sorting = QCheckBox("Live sorting (allow rows to move as speed/status changes)")
        self.live_sorting.setChecked(bool(cfg.get("live_sorting", False)))
        self.integrate = QCheckBox("Use RemoteQBT for magnet links and .torrent files")
        self.integrate.setChecked(bool(cfg.get("integrate_windows", True)))
        uil.addWidget(self.live_sorting)
        uil.addWidget(self.integrate)
        note = QLabel("Live sorting is OFF by default so background refreshes never yank the torrent list away from what you are inspecting.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{C_MUTED};")
        uil.addWidget(note); uil.addStretch()
        tabs.addTab(ui, "Interface")

        testrow = QHBoxLayout()
        self.test_btn = QPushButton("Test connection")
        self.test_result = QLabel("")
        testrow.addWidget(self.test_btn); testrow.addWidget(self.test_result, 1)
        root.addLayout(testrow)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        root.addWidget(buttons)
        self.test_btn.clicked.connect(self.test_connection)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

    def values(self) -> dict[str, Any]:
        return {
            "server": self.server.text().strip().rstrip("/"),
            "api_key": self.key.text().strip(),
            "save_path": self.save_path.text().strip(),
            "smb_path": self.smb_path.text().strip(),
            "refresh_ms": int(self.refresh.currentData()),
            "live_sorting": self.live_sorting.isChecked(),
            "show_properties": self.cfg.get("show_properties", True),
            "show_sidebar": self.cfg.get("show_sidebar", True),
            "integrate_windows": self.integrate.isChecked(),
        }

    def test_connection(self):
        self.test_result.setText("Testing…")
        QApplication.processEvents()
        try:
            client = QbtClient(self.values())
            version = client.version()
            api = client.webapi_version()
            self.test_result.setText(f"Connected ✓  qBittorrent {version} · Web API {api}")
            self.test_result.setStyleSheet(f"color:{C_GREEN};")
        except Exception as e:
            self.test_result.setText(str(e))
            self.test_result.setStyleSheet(f"color:{C_RED};")

    def save(self):
        cfg = self.values()
        if not cfg["server"] or not cfg["api_key"] or not cfg["save_path"]:
            QMessageBox.warning(self, APP_NAME, "Server, API key, and remote save path are required.")
            return
        try:
            save_config(cfg)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save settings:\n\n{e}")
            return
        self.result_cfg = cfg
        self.accept()


class SpeedLimitsDialog(QDialog):
    def __init__(self, values: dict[str, int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Global Rate Limits")
        root = QVBoxLayout(self)
        form = QFormLayout(); root.addLayout(form)
        self.widgets: dict[str, QSpinBox] = {}
        labels = [
            ("dl_limit", "Download limit:"), ("up_limit", "Upload limit:"),
            ("alt_dl_limit", "Alternative download:"), ("alt_up_limit", "Alternative upload:"),
        ]
        for key, label in labels:
            w = QSpinBox(); w.setRange(0, 10_000_000); w.setSuffix(" KiB/s"); w.setSpecialValueText("Unlimited")
            raw = int(values.get(key, 0) or 0); w.setValue(0 if raw <= 0 else raw // 1024)
            self.widgets[key] = w; form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); root.addWidget(bb)

    def values(self) -> dict[str, int]:
        def raw(key: str) -> int:
            v = self.widgets[key].value()
            return 0 if v == 0 else v * 1024
        return {k: raw(k) for k in self.widgets}


class TorrentLimitsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Torrent Rate Limits")
        root = QFormLayout(self)
        self.dl = QSpinBox(); self.dl.setRange(0, 10_000_000); self.dl.setSuffix(" KiB/s"); self.dl.setSpecialValueText("Unlimited")
        self.up = QSpinBox(); self.up.setRange(0, 10_000_000); self.up.setSuffix(" KiB/s"); self.up.setSpecialValueText("Unlimited")
        root.addRow("Download limit:", self.dl); root.addRow("Upload limit:", self.up)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); root.addRow(bb)

    def limits(self) -> tuple[int, int]:
        return (0 if self.dl.value() == 0 else self.dl.value() * 1024, 0 if self.up.value() == 0 else self.up.value() * 1024)


class ShareLimitsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Share Ratio Limiting")
        root = QFormLayout(self)
        self.ratio = QDoubleSpinBox(); self.ratio.setRange(-2, 10000); self.ratio.setDecimals(2); self.ratio.setValue(-2); self.ratio.setSpecialValueText("Use global")
        self.time = QSpinBox(); self.time.setRange(-2, 10_000_000); self.time.setValue(-2); self.time.setSuffix(" min"); self.time.setSpecialValueText("Use global")
        self.inactive = QSpinBox(); self.inactive.setRange(-2, 10_000_000); self.inactive.setValue(-2); self.inactive.setSuffix(" min"); self.inactive.setSpecialValueText("Use global")
        self.action = QComboBox();
        for label, value in [("Use global", "Default"), ("Stop", "Stop"), ("Remove", "Remove"), ("Remove and delete files", "RemoveWithContent"), ("Enable Super Seeding", "EnableSuperSeeding")]:
            self.action.addItem(label, value)
        root.addRow("Ratio (-1 = unlimited):", self.ratio)
        root.addRow("Seeding time:", self.time)
        root.addRow("Inactive seeding time:", self.inactive)
        root.addRow("Action:", self.action)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); root.addRow(bb)

    def values(self):
        return self.ratio.value(), self.time.value(), self.inactive.value(), self.action.currentData()


def _multiline_text_dialog(parent, title: str, label: str, initial: str = "") -> tuple[str, bool]:
    d = QDialog(parent); d.setWindowTitle(title); d.resize(560, 330)
    root = QVBoxLayout(d); root.addWidget(QLabel(label))
    edit = QPlainTextEdit(); edit.setPlainText(initial); root.addWidget(edit, 1)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(d.accept); bb.rejected.connect(d.reject); root.addWidget(bb)
    ok = d.exec() == QDialog.DialogCode.Accepted
    return edit.toPlainText(), ok


def single_text_dialog(parent, title: str, label: str, initial: str = "") -> tuple[str, bool]:
    d = QDialog(parent); d.setWindowTitle(title)
    root = QVBoxLayout(d); root.addWidget(QLabel(label))
    edit = QLineEdit(initial); edit.selectAll(); root.addWidget(edit)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(d.accept); bb.rejected.connect(d.reject); root.addWidget(bb)
    edit.setFocus()
    ok = d.exec() == QDialog.DialogCode.Accepted
    return edit.text(), ok

class QbtPreferencesDialog(QDialog):
    """Compact native editor for the remotely writable qBittorrent preferences
    that are most useful from a transfer client. It deliberately leaves obscure
    daemon/web/security settings to the official Web UI so RemoteQBT cannot
    accidentally disconnect itself from the remote host.
    """
    def __init__(self, client: QbtClient, prefs: dict[str, Any], parent=None):
        super().__init__(parent)
        self.client = client
        self.prefs = dict(prefs)
        self.setWindowTitle("Options — remote qBittorrent")
        self.resize(700, 590)
        root = QVBoxLayout(self)
        tabs = QTabWidget(); root.addWidget(tabs, 1)

        # Downloads
        page = QWidget(); form = QFormLayout(page)
        pathrow = QHBoxLayout(); self.q_save = QLineEdit(str(prefs.get("save_path", DEFAULT_SAVE_PATH))); browse = QPushButton("Browse remote host…")
        pathrow.addWidget(self.q_save,1); pathrow.addWidget(browse); wrap=QWidget(); wrap.setLayout(pathrow); form.addRow("Default Save Path:", wrap)
        self.q_temp_enable = QCheckBox("Use another path for incomplete torrents"); self.q_temp_enable.setChecked(bool(prefs.get("temp_path_enabled", False)))
        temprow=QHBoxLayout(); self.q_temp=QLineEdit(str(prefs.get("temp_path", ""))); tempbrowse=QPushButton("Browse remote host…"); temprow.addWidget(self.q_temp,1); temprow.addWidget(tempbrowse); tw=QWidget(); tw.setLayout(temprow); form.addRow(self.q_temp_enable, tw)
        self.q_auto_tmm=QCheckBox("Automatic Torrent Management by default"); self.q_auto_tmm.setChecked(bool(prefs.get("auto_tmm_enabled", False)))
        self.q_top=QCheckBox("Add new torrents to top of queue"); self.q_top.setChecked(bool(prefs.get("add_to_top_of_queue", False)))
        self.q_stopped=QCheckBox("Add torrents in stopped state"); self.q_stopped.setChecked(bool(prefs.get("add_stopped_enabled", False)))
        self.q_prealloc=QCheckBox("Pre-allocate disk space for all files"); self.q_prealloc.setChecked(bool(prefs.get("preallocate_all", False)))
        self.q_incomplete_ext=QCheckBox("Append .!qB extension to incomplete files"); self.q_incomplete_ext.setChecked(bool(prefs.get("incomplete_files_ext", False)))
        self.q_merge_trackers=QCheckBox("Always announce to all trackers in a tier / merge trackers"); self.q_merge_trackers.setChecked(bool(prefs.get("merge_trackers", False)))
        self.q_layout=QComboBox(); self.q_layout.addItem("Original", "Original"); self.q_layout.addItem("Create subfolder", "Subfolder"); self.q_layout.addItem("Don't create subfolder", "NoSubfolder")
        ix=self.q_layout.findData(str(prefs.get("torrent_content_layout", "Original"))); self.q_layout.setCurrentIndex(max(0,ix))
        for w in (self.q_auto_tmm,self.q_top,self.q_stopped,self.q_prealloc,self.q_incomplete_ext,self.q_merge_trackers): form.addRow("",w)
        form.addRow("Default content layout:",self.q_layout)
        browse.clicked.connect(lambda:self._pick(self.q_save)); tempbrowse.clicked.connect(lambda:self._pick(self.q_temp)); tabs.addTab(page,"Downloads")

        # Connection
        page=QWidget(); form=QFormLayout(page)
        self.q_port=QSpinBox(); self.q_port.setRange(1,65535); self.q_port.setValue(int(prefs.get("listen_port",6881) or 6881)); form.addRow("Port used for incoming connections:",self.q_port)
        self.q_upnp=QCheckBox("Use UPnP / NAT-PMP port forwarding"); self.q_upnp.setChecked(bool(prefs.get("upnp",True))); form.addRow("",self.q_upnp)
        self.q_max_conn=QSpinBox(); self.q_max_conn.setRange(-1,1_000_000); self.q_max_conn.setValue(int(prefs.get("max_connec",500) or 0)); self.q_max_conn.setSpecialValueText("Unlimited")
        self.q_max_conn_t=QSpinBox(); self.q_max_conn_t.setRange(-1,1_000_000); self.q_max_conn_t.setValue(int(prefs.get("max_connec_per_torrent",100) or 0)); self.q_max_conn_t.setSpecialValueText("Unlimited")
        self.q_max_upload=QSpinBox(); self.q_max_upload.setRange(-1,1_000_000); self.q_max_upload.setValue(int(prefs.get("max_uploads",20) or 0)); self.q_max_upload.setSpecialValueText("Unlimited")
        self.q_max_upload_t=QSpinBox(); self.q_max_upload_t.setRange(-1,1_000_000); self.q_max_upload_t.setValue(int(prefs.get("max_uploads_per_torrent",4) or 0)); self.q_max_upload_t.setSpecialValueText("Unlimited")
        form.addRow("Global maximum connections:",self.q_max_conn); form.addRow("Maximum connections per torrent:",self.q_max_conn_t); form.addRow("Global maximum upload slots:",self.q_max_upload); form.addRow("Maximum upload slots per torrent:",self.q_max_upload_t)
        tabs.addTab(page,"Connection")

        # Speed
        page=QWidget(); form=QFormLayout(page)
        def rate(value):
            w=QSpinBox(); w.setRange(0,10_000_000); w.setSuffix(" KiB/s"); w.setSpecialValueText("Unlimited"); raw=int(value or 0); w.setValue(0 if raw <= 0 else raw//1024); return w
        self.q_dl=rate(prefs.get("dl_limit",0)); self.q_ul=rate(prefs.get("up_limit",0)); self.q_alt_dl=rate(prefs.get("alt_dl_limit",0)); self.q_alt_ul=rate(prefs.get("alt_up_limit",0))
        form.addRow("Global download limit:",self.q_dl); form.addRow("Global upload limit:",self.q_ul); form.addRow("Alternative download limit:",self.q_alt_dl); form.addRow("Alternative upload limit:",self.q_alt_ul)
        tabs.addTab(page,"Speed")

        # BitTorrent / Queueing
        page=QWidget(); form=QFormLayout(page)
        self.q_dht=QCheckBox("Enable DHT"); self.q_dht.setChecked(bool(prefs.get("dht",True)))
        self.q_pex=QCheckBox("Enable Peer Exchange (PeX)"); self.q_pex.setChecked(bool(prefs.get("pex",True)))
        self.q_lsd=QCheckBox("Enable Local Peer Discovery"); self.q_lsd.setChecked(bool(prefs.get("lsd",True)))
        self.q_anon=QCheckBox("Enable anonymous mode"); self.q_anon.setChecked(bool(prefs.get("anonymous_mode",False)))
        self.q_queue=QCheckBox("Enable torrent queueing"); self.q_queue.setChecked(bool(prefs.get("queueing_enabled",True)))
        self.q_max_dl=QSpinBox(); self.q_max_dl.setRange(0,100000); self.q_max_dl.setValue(int(prefs.get("max_active_downloads",3) or 0))
        self.q_max_ul=QSpinBox(); self.q_max_ul.setRange(0,100000); self.q_max_ul.setValue(int(prefs.get("max_active_uploads",3) or 0))
        self.q_max_t=QSpinBox(); self.q_max_t.setRange(0,100000); self.q_max_t.setValue(int(prefs.get("max_active_torrents",5) or 0))
        for w in (self.q_dht,self.q_pex,self.q_lsd,self.q_anon,self.q_queue): form.addRow("",w)
        form.addRow("Maximum active downloads:",self.q_max_dl); form.addRow("Maximum active uploads:",self.q_max_ul); form.addRow("Maximum active torrents:",self.q_max_t)
        tabs.addTab(page,"BitTorrent")

        warn=QLabel("These settings are applied to the remote qBittorrent instance, not to this PC. Web UI/security settings are intentionally excluded so RemoteQBT cannot accidentally cut off its own remote connection."); warn.setWordWrap(True); warn.setStyleSheet(f"color:{C_MUTED};"); root.addWidget(warn)
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); bb.button(QDialogButtonBox.StandardButton.Ok).setText("Apply"); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); root.addWidget(bb)

    def _pick(self, target: QLineEdit):
        d=RemotePathDialog(self.client,target.text().strip() or DEFAULT_SAVE_PATH,self)
        if d.exec()==QDialog.DialogCode.Accepted: target.setText(d.selected_path)

    @staticmethod
    def _rate_value(w: QSpinBox) -> int:
        return 0 if w.value()==0 else w.value()*1024

    def updates(self) -> dict[str, Any]:
        return {
            "save_path":self.q_save.text().strip(), "temp_path_enabled":self.q_temp_enable.isChecked(), "temp_path":self.q_temp.text().strip(),
            "auto_tmm_enabled":self.q_auto_tmm.isChecked(), "add_to_top_of_queue":self.q_top.isChecked(), "add_stopped_enabled":self.q_stopped.isChecked(),
            "preallocate_all":self.q_prealloc.isChecked(), "incomplete_files_ext":self.q_incomplete_ext.isChecked(), "merge_trackers":self.q_merge_trackers.isChecked(), "torrent_content_layout":self.q_layout.currentData(),
            "listen_port":self.q_port.value(), "upnp":self.q_upnp.isChecked(), "max_connec":self.q_max_conn.value(), "max_connec_per_torrent":self.q_max_conn_t.value(), "max_uploads":self.q_max_upload.value(), "max_uploads_per_torrent":self.q_max_upload_t.value(),
            "dl_limit":self._rate_value(self.q_dl), "up_limit":self._rate_value(self.q_ul), "alt_dl_limit":self._rate_value(self.q_alt_dl), "alt_up_limit":self._rate_value(self.q_alt_ul),
            "dht":self.q_dht.isChecked(), "pex":self.q_pex.isChecked(), "lsd":self.q_lsd.isChecked(), "anonymous_mode":self.q_anon.isChecked(),
            "queueing_enabled":self.q_queue.isChecked(), "max_active_downloads":self.q_max_dl.value(), "max_active_uploads":self.q_max_ul.value(), "max_active_torrents":self.q_max_t.value(),
        }

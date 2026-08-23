from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QItemSelectionModel, QModelIndex, QObject, QPoint, QRunnable, QSettings, QSize,
    QThreadPool, QTimer, Qt, Signal, QUrl,
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QProgressDialog, QPushButton,
    QSizePolicy, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionProgressBar,
    QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .api import QbtApiError, QbtClient
from .common import (
    APP_NAME, DISPLAY_VERSION, C_GREEN, C_ORANGE, C_RED, C_MUTED, DEFAULT_REFRESH_MS,
    DEFAULT_SAVE_PATH, DEFAULT_SERVER, DEFAULT_SMB_PATH, format_timestamp, human_bytes,
    human_eta, human_speed, make_app_icon, qbt_icon,
)
from .config import load_config, save_config
from .dialogs import (
    AddTorrentDialog, QbtPreferencesDialog, RemotePathDialog, SettingsDialog, ShareLimitsDialog,
    SpeedLimitsDialog, TorrentLimitsDialog, _multiline_text_dialog, single_text_dialog,
)
from .models import COLUMNS, HASH_ROLE, PROGRESS_ROLE, TorrentProxyModel, TorrentTableModel
from .windows_integration import open_default_apps, register_associations, unregister_associations
from .updater import (
    RELEASES_PAGE, UPDATE_LOG_FILE, UpdateInfo, check_for_update, download_update,
    launch_installer,
)

log = logging.getLogger("RemoteQBT")


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(self.fn())
        except Exception as e:
            log.exception("Worker failed")
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() != 3:
            return super().paint(painter, option, index)
        try:
            value = float(index.data(PROGRESS_ROLE) or 0)
        except Exception:
            value = 0.0
        opt = QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(4, 5, -4, -5)
        opt.minimum = 0
        opt.maximum = 1000
        opt.progress = max(0, min(1000, int(value * 10)))
        opt.text = f"{value:.1f}%"
        opt.textVisible = True
        opt.textAlignment = Qt.AlignmentFlag.AlignCenter
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, opt, painter)


class SyncState:
    def __init__(self):
        self.rid = 0
        self.torrents: dict[str, dict[str, Any]] = {}
        self.categories: dict[str, dict[str, Any]] = {}
        self.tags: list[str] = []
        self.trackers: dict[str, list[str]] = {}
        self.server_state: dict[str, Any] = {}

    def reset(self):
        self.__init__()

    def apply(self, data: dict[str, Any]) -> bool:
        first = self.rid == 0 or bool(data.get("full_update"))
        if data.get("full_update"):
            self.torrents = {}
            self.categories = {}
            self.tags = []
            self.trackers = {}
            self.server_state = {}

        for h, patch in (data.get("torrents", {}) or {}).items():
            if h not in self.torrents or data.get("full_update"):
                self.torrents[h] = dict(patch)
            else:
                self.torrents[h].update(patch)
            self.torrents[h]["hash"] = h
        for h in data.get("torrents_removed", []) or []:
            self.torrents.pop(str(h), None)

        for name, patch in (data.get("categories", {}) or {}).items():
            if name not in self.categories or data.get("full_update"):
                self.categories[name] = dict(patch)
            else:
                self.categories[name].update(patch)
        for name in data.get("categories_removed", []) or []:
            self.categories.pop(str(name), None)

        if data.get("full_update") and "tags" in data:
            self.tags = [str(x) for x in data.get("tags", [])]
        else:
            for tag in data.get("tags", []) or []:
                if str(tag) not in self.tags:
                    self.tags.append(str(tag))
            removed = {str(x) for x in data.get("tags_removed", []) or []}
            if removed:
                self.tags = [x for x in self.tags if x not in removed]

        for tracker, hashes in (data.get("trackers", {}) or {}).items():
            self.trackers[str(tracker)] = [str(x) for x in hashes]
        for tracker in data.get("trackers_removed", []) or []:
            self.trackers.pop(str(tracker), None)

        self.server_state.update(data.get("server_state", {}) or {})
        self.rid = int(data.get("rid", self.rid) or self.rid)
        return first


class MainWindow(QMainWindow):
    def __init__(self, launch_sources: list[str] | None = None):
        super().__init__()
        self.cfg = load_config()
        self.client_obj = QbtClient(self.cfg)
        self.sync = SyncState()
        self.version = ""
        self.webapi_version = ""
        self.refresh_busy = False
        self.detail_busy = False
        self.last_detail_hash = ""
        self.last_detail_data: dict[str, Any] = {}
        self.pending_launch_sources = launch_sources or []
        self.threadpool = QThreadPool.globalInstance()
        self.threadpool.setMaxThreadCount(4)
        self._workers: set[Worker] = set()
        self.settings_store = QSettings(APP_NAME, APP_NAME)
        self.pending_update: UpdateInfo | None = None
        self.update_busy = False
        self.update_progress: QProgressDialog | None = None

        self.setWindowIcon(make_app_icon())
        self.setWindowTitle(f"RemoteQBT — qBittorrent on {self.remote_name()}")
        self.resize(1420, 850)
        self.setMinimumSize(1020, 650)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self.update_action_states()
        self._restore_ui()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_sync)
        self.details_timer = QTimer(self)
        self.details_timer.setInterval(5000)
        self.details_timer.timeout.connect(self.refresh_selected_details)
        self.details_timer.start()
        self.apply_refresh_interval()

        if not self.cfg.get("api_key"):
            QTimer.singleShot(200, self.open_settings)
        else:
            QTimer.singleShot(100, lambda: self.refresh_sync(full=True))
        if self.pending_launch_sources:
            QTimer.singleShot(550, self.consume_launch_sources)
        # Lightweight GitHub Releases check, throttled to twice a day. It runs
        # after the qBittorrent dashboard is already usable and never blocks UI.
        QTimer.singleShot(7000, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------------
    # Actions and qBittorrent-shaped UI
    # ------------------------------------------------------------------
    def _action(self, text, icon=None, shortcut=None, slot=None, *, checkable=False, checked=False, tip=None):
        a = QAction(qbt_icon(icon), text, self)
        if shortcut: a.setShortcut(shortcut)
        if slot: a.triggered.connect(slot)
        if checkable:
            a.setCheckable(True); a.setChecked(checked)
        if tip:
            a.setToolTip(tip); a.setStatusTip(tip)
        return a

    def _build_actions(self):
        # Desktop-mainwindow actions that have a meaningful remote equivalent.
        self.act_add_file = self._action("Add Torrent File…", "list-add.svg", "Ctrl+O", self.add_torrent_files)
        self.act_add_link = self._action("Add Torrent Link…", "insert-link.svg", "Ctrl+Shift+O", self.add_torrent_links)
        self.act_remove = self._action("Remove", "list-remove.svg", "Delete", self.delete_selected)
        self.act_start = self._action("Start", "torrent-start.svg", slot=lambda: self.basic_action("start"))
        self.act_stop = self._action("Stop", "torrent-stop.svg", slot=lambda: self.basic_action("stop"))
        self.act_resume_session = self._action("Resume Session", "torrent-start.svg", slot=lambda: self.session_action("start"))
        self.act_pause_session = self._action("Pause Session", "pause-session.svg", slot=lambda: self.session_action("stop"))
        self.act_open_folder = self._action("Open Destination Folder", "folder-remote.svg", slot=self.open_selected_folder)
        self.act_q_top = self._action("Top of Queue", "go-top.svg", slot=lambda: self.queue_action("top"))
        self.act_q_up = self._action("Move Up Queue", "go-up.svg", slot=lambda: self.queue_action("up"))
        self.act_q_down = self._action("Move Down Queue", "go-down.svg", slot=lambda: self.queue_action("down"))
        self.act_q_bottom = self._action("Bottom of Queue", "go-bottom.svg", slot=lambda: self.queue_action("bottom"))
        self.act_recheck = self._action("Force Recheck", "force-recheck.svg", slot=lambda: self.basic_action("recheck"))
        self.act_reannounce = self._action("Force Reannounce", "reannounce.svg", slot=lambda: self.basic_action("reannounce"))
        self.act_options = self._action("Options…", "configure.svg", "Alt+O", self.open_qbt_options)
        self.act_connection = self._action("RemoteQBT Connection…", slot=self.open_settings)
        self.act_refresh = self._action("Refresh", shortcut="F5", slot=lambda: self.refresh_sync(full=True))
        self.act_webui = self._action("Open qBittorrent Web UI", slot=self.open_webui)
        self.act_exit = self._action("Exit", shortcut="Alt+F4", slot=self.close)
        self.act_update = self._action("Check for RemoteQBT Updates…", slot=lambda: self.check_updates(manual=True))
        self.act_releases = self._action("RemoteQBT Releases on GitHub", slot=lambda: QDesktopServices.openUrl(QUrl(RELEASES_PAGE)))
        self.act_update_log = self._action("Open RemoteQBT Update Log", slot=self.open_update_log)
        self.act_about = self._action("About RemoteQBT", slot=self.about)

        self.act_sidebar = self._action("Filters Sidebar", checkable=True, checked=bool(self.cfg.get("show_sidebar", True)), slot=self.toggle_sidebar)
        self.act_properties = self._action("Torrent Properties", checkable=True, checked=bool(self.cfg.get("show_properties", True)), slot=self.toggle_properties)
        self.act_live_sort = self._action(
            "Live Sorting", checkable=True, checked=bool(self.cfg.get("live_sorting", False)), slot=self.set_live_sorting,
            tip="Allow rows to re-sort as live values change. Off keeps your current view stable."
        )

        self.act_force_start = self._action("Force Start", checkable=True, slot=self.toggle_force_start)
        self.act_super_seed = self._action("Super Seeding Mode", checkable=True, slot=self.toggle_super_seeding)
        self.act_sequential = self._action("Download in Sequential Order", checkable=True, slot=self.toggle_sequential)
        self.act_first_last = self._action("Download First and Last Pieces First", checkable=True, slot=self.toggle_first_last)
        self.act_auto_tmm = self._action("Automatic Torrent Management", checkable=True, slot=self.toggle_auto_tmm)
        self.act_rename = self._action("Rename…", slot=self.rename_selected)
        self.act_comment = self._action("Set Comment…", slot=self.comment_selected)
        self.act_location = self._action("Set Location…", "set-location.svg", slot=self.set_location)
        self.act_save_path = self._action("Set Save Path…", slot=self.set_save_path)
        self.act_download_path = self._action("Set Download Path…", slot=self.set_download_path)
        self.act_torrent_limits = self._action("Set Torrent Rate Limits…", slot=self.set_torrent_limits)
        self.act_share_limits = self._action("Share Ratio Limiting…", slot=self.set_share_limits)
        self.act_global_limits = self._action("Set Global Speed Limits…", slot=self.set_global_limits)
        self.act_alt_speed = self._action("Alternative Speed Limits", "slow.svg", checkable=True, slot=self.toggle_alt_speed)
        self.act_stats = self._action("Statistics", slot=self.show_statistics)
        self.act_log = self._action("Execution Log", slot=self.show_log)
        self.act_shutdown = self._action("Exit Remote qBittorrent…", slot=self.shutdown_remote)
        self.act_export = self._action("Export .torrent…", slot=self.export_selected)
        self.act_register = self._action("Use RemoteQBT for .torrent + magnet", slot=self.register_windows)
        self.act_defaults = self._action("Windows Default Apps…", slot=open_default_apps)

    def _build_menus(self):
        mb = self.menuBar()
        filem = mb.addMenu("&File")
        filem.addAction(self.act_add_file); filem.addAction(self.act_add_link)
        filem.addSeparator(); filem.addAction(self.act_open_folder); filem.addAction(self.act_export); filem.addSeparator(); filem.addAction(self.act_exit)

        editm = mb.addMenu("&Edit")
        editm.addAction(self.act_start); editm.addAction(self.act_stop); editm.addSeparator(); editm.addAction(self.act_remove)
        editm.addSeparator(); editm.addAction(self.act_pause_session); editm.addAction(self.act_resume_session)
        editm.addSeparator(); editm.addAction(self.act_q_top); editm.addAction(self.act_q_up); editm.addAction(self.act_q_down); editm.addAction(self.act_q_bottom)
        editm.addSeparator(); editm.addAction(self.act_recheck); editm.addAction(self.act_reannounce)
        editm.addSeparator(); editm.addAction(self.act_force_start); editm.addAction(self.act_super_seed); editm.addAction(self.act_sequential); editm.addAction(self.act_first_last); editm.addAction(self.act_auto_tmm)
        editm.addSeparator(); editm.addAction(self.act_rename); editm.addAction(self.act_comment)
        editm.addSeparator(); editm.addAction(self.act_location); editm.addAction(self.act_save_path); editm.addAction(self.act_download_path)
        editm.addSeparator(); editm.addAction(self.act_torrent_limits); editm.addAction(self.act_share_limits)

        viewm = mb.addMenu("&View")
        viewm.addAction(self.act_sidebar); viewm.addAction(self.act_properties); viewm.addAction(self.act_live_sort)
        viewm.addSeparator(); viewm.addAction(self.act_refresh); viewm.addAction(self.act_webui)
        logm = viewm.addMenu("Log")
        logm.addAction(self.act_log)
        viewm.addSeparator(); viewm.addAction(self.act_stats)

        toolsm = mb.addMenu("&Tools")
        toolsm.addAction(self.act_global_limits); toolsm.addAction(self.act_alt_speed)
        toolsm.addSeparator(); toolsm.addAction(self.act_register); toolsm.addAction(self.act_defaults)
        toolsm.addSeparator(); toolsm.addAction(self.act_options); toolsm.addAction(self.act_connection)
        toolsm.addSeparator(); toolsm.addAction(self.act_shutdown)

        helpm = mb.addMenu("&Help")
        helpm.addAction(self.act_update); helpm.addAction(self.act_releases); helpm.addAction(self.act_update_log); helpm.addSeparator(); helpm.addAction(self.act_about)

    def _build_toolbar(self):
        tb = QToolBar("Top Toolbar", self)
        tb.setObjectName("TopToolbar")
        tb.setMovable(False); tb.setFloatable(False); tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        for a in (self.act_add_file, self.act_add_link, self.act_remove): tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_start, self.act_stop, self.act_open_folder): tb.addAction(a)
        for a in (self.act_q_top, self.act_q_up, self.act_q_down, self.act_q_bottom): tb.addAction(a)
        tb.addSeparator(); tb.addAction(self.act_recheck); tb.addAction(self.act_reannounce)
        tb.addSeparator(); tb.addAction(self.act_options)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); tb.addWidget(spacer)
        self.search = QLineEdit(); self.search.setPlaceholderText("Filter torrents…"); self.search.setClearButtonEnabled(True); self.search.setFixedWidth(260)
        self.search.textChanged.connect(self.search_changed); tb.addWidget(self.search)

    def _build_central(self):
        central = QWidget(); self.setCentralWidget(central)
        outer = QVBoxLayout(central); outer.setContentsMargins(5, 3, 5, 0); outer.setSpacing(3)
        self.main_split = QSplitter(Qt.Orientation.Horizontal); outer.addWidget(self.main_split, 1)

        self.sidebar = QTreeWidget(); self.sidebar.setHeaderHidden(True); self.sidebar.setMinimumWidth(180); self.sidebar.setMaximumWidth(340)
        self.sidebar.itemSelectionChanged.connect(self.sidebar_selection_changed)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self.sidebar_context_menu)
        self.main_split.addWidget(self.sidebar)
        self.sidebar.setVisible(self.act_sidebar.isChecked())

        self.right_split = QSplitter(Qt.Orientation.Vertical); self.main_split.addWidget(self.right_split); self.main_split.setStretchFactor(1, 1); self.main_split.setSizes([215, 1150])

        self.model = TorrentTableModel(self)
        self.proxy = TorrentProxyModel(self); self.proxy.setSourceModel(self.model)
        self.proxy.set_live_sorting(bool(self.cfg.get("live_sorting", False)))
        self.table = QTableView(); self.table.setModel(self.proxy); self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setAlternatingRowColors(True); self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.verticalHeader().setVisible(False); self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.table.customContextMenuRequested.connect(self.open_table_menu)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed())
        header = self.table.horizontalHeader(); header.setStretchLastSection(False); header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        widths = [42, 390, 90, 125, 125, 80, 80, 105, 105, 85, 70, 135]
        for i, w in enumerate(widths):
            if i != 1: header.resizeSection(i, w)
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        self.right_split.addWidget(self.table)

        self.tabs = QTabWidget(); self.tabs.setMinimumHeight(205); self.tabs.setVisible(self.act_properties.isChecked())
        self.general = self._detail_table(["Property", "Value"], stretch=1)
        self.trackers = self._detail_table(["URL", "Status", "Seeds", "Peers", "Message"], stretch=0)
        self.peers = self._detail_table(["IP", "Port", "Client", "Progress", "Down Speed", "Up Speed", "Flags"], stretch=2)
        self.webseeds = self._detail_table(["URL"], stretch=0)
        self.content = self._detail_table(["#", "Name", "Size", "Progress", "Priority"], stretch=1)
        self.tabs.addTab(self.general, "General"); self.tabs.addTab(self.trackers, "Trackers"); self.tabs.addTab(self.peers, "Peers"); self.tabs.addTab(self.webseeds, "HTTP Sources"); self.tabs.addTab(self.content, "Content")
        self.tabs.currentChanged.connect(lambda _index: QTimer.singleShot(50, self.refresh_selected_details))
        self.trackers.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.trackers.customContextMenuRequested.connect(self.trackers_menu)
        self.peers.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.peers.customContextMenuRequested.connect(self.peers_menu)
        self.webseeds.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.webseeds.customContextMenuRequested.connect(self.webseeds_menu)
        self.content.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.content.customContextMenuRequested.connect(self.content_menu)
        self.right_split.addWidget(self.tabs); self.right_split.setSizes([585, 230]); self.right_split.setStretchFactor(0, 1)

    def _detail_table(self, headers: list[str], stretch: int | None = None):
        table = QTableWidget(0, len(headers)); table.setHorizontalHeaderLabels(headers); table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        if stretch is not None: table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        return table

    def _build_statusbar(self):
        sb = self.statusBar()
        self.status_remote = QLabel(f" ● {self.remote_name()} ")
        self.status_msg = QLabel(" Ready ")
        self.status_update = QLabel("")
        self.status_update.setTextFormat(Qt.TextFormat.RichText)
        self.status_update.setOpenExternalLinks(False)
        self.status_update.linkActivated.connect(lambda _href: self.install_pending_update())
        self.status_disk = QLabel(" Free: — ")
        self.status_dht = QLabel(" DHT: — ")
        self.status_dl = QLabel(" ↓ 0 B/s ")
        self.status_up = QLabel(" ↑ 0 B/s ")
        sb.addWidget(self.status_remote); sb.addWidget(self.status_msg, 1)
        sb.addPermanentWidget(self.status_update); sb.addPermanentWidget(self.status_disk); sb.addPermanentWidget(self.status_dht); sb.addPermanentWidget(self.status_dl); sb.addPermanentWidget(self.status_up)

    # ------------------------------------------------------------------
    # State and workers
    # ------------------------------------------------------------------
    def _restore_ui(self):
        geo = self.settings_store.value("geometry"); state = self.settings_store.value("windowState")
        if geo: self.restoreGeometry(geo)
        if state: self.restoreState(state)

    def closeEvent(self, event):
        self.settings_store.setValue("geometry", self.saveGeometry()); self.settings_store.setValue("windowState", self.saveState())
        event.accept()

    def run_worker(self, fn, on_result=None, on_error=None, on_finished=None):
        worker = Worker(fn); self._workers.add(worker)
        def result(value):
            try:
                if on_result: on_result(value)
            except Exception as e:
                log.exception("GUI result handler failed"); self.show_error(f"GUI update error: {e}")
        def error(message):
            try: (on_error or self.show_error)(message)
            except Exception: log.exception("GUI error handler failed")
        def finish():
            try:
                if on_finished: on_finished()
            finally:
                self._workers.discard(worker)
        worker.signals.result.connect(result); worker.signals.error.connect(error); worker.signals.finished.connect(finish)
        self.threadpool.start(worker); return worker

    def remote_name(self) -> str:
        try:
            host = QUrl(str(self.cfg.get("server", DEFAULT_SERVER))).host().strip()
            return host or "remote host"
        except Exception:
            return "remote host"

    def client(self) -> QbtClient:
        return self.client_obj

    def apply_refresh_interval(self):
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.setInterval(int(self.cfg.get("refresh_ms", DEFAULT_REFRESH_MS)))
            if self.cfg.get("api_key"): self.refresh_timer.start()

    def show_error(self, message: str):
        self.status_msg.setText(f" Error: {message} ")
        self.status_remote.setText(" ● Disconnected "); self.status_remote.setStyleSheet(f"color:{C_RED};")
        log.warning(message)

    # ------------------------------------------------------------------
    # Incremental sync; never rebuild the transfer list
    # ------------------------------------------------------------------
    def refresh_sync(self, full: bool = False):
        if self.refresh_busy or not self.cfg.get("api_key"): return
        if full: self.sync.rid = 0
        self.refresh_busy = True
        c = self.client(); rid = self.sync.rid
        self.run_worker(lambda: c.main_sync(rid), self._sync_received, self.show_error, lambda: setattr(self, "refresh_busy", False))

    def _sync_received(self, data: dict[str, Any]):
        first = self.sync.apply(data)
        if not self.version:
            self.run_worker(lambda: (self.client().version(), self.client().webapi_version()), self._version_received, lambda m: log.warning("Version: %s", m))

        selected = self.selected_hashes(); vscroll = self.table.verticalScrollBar().value(); hscroll = self.table.horizontalScrollBar().value()
        self.model.replace_snapshot(self.sync.torrents, first_load_sort=first)
        self.proxy.invalidateFilter()
        # With Live Sorting off, QSortFilterProxyModel intentionally leaves the
        # established row order alone as live values change.
        self.restore_selection(selected)
        self.update_action_states()
        self.table.verticalScrollBar().setValue(vscroll); self.table.horizontalScrollBar().setValue(hscroll)
        self.rebuild_sidebar()
        self.update_statusbar()

    def _version_received(self, value):
        self.version, self.webapi_version = value
        self.update_statusbar()

    def update_statusbar(self):
        state = self.sync.server_state
        dl = int(state.get("dl_info_speed", 0) or 0); up = int(state.get("up_info_speed", 0) or 0); dht = int(state.get("dht_nodes", 0) or 0)
        free = int(state.get("free_space_on_disk", -1) or -1); conn = str(state.get("connection_status", "connected") or "connected")
        self.status_remote.setText(f" ● {self.remote_name()} " if conn == "connected" else f" ● {self.remote_name()} · {conn.title()} ")
        self.status_remote.setStyleSheet(f"color:{C_GREEN if conn == 'connected' else C_ORANGE};")
        self.status_dl.setText(f" ↓ {human_speed(dl)} "); self.status_up.setText(f" ↑ {human_speed(up)} "); self.status_dht.setText(f" DHT: {dht} ")
        self.status_disk.setText(f" Free: {human_bytes(free)} " if free >= 0 else " Free: — ")
        self.status_msg.setText(f" {self.proxy.rowCount()} shown / {len(self.sync.torrents)} torrents · updated {time.strftime('%H:%M:%S')} ")
        qv = f"qBittorrent {self.version}" if self.version else "qBittorrent"
        self.setWindowTitle(f"RemoteQBT — {qv} on {self.remote_name()}   [D: {human_speed(dl)}, U: {human_speed(up)}]")
        self.act_alt_speed.blockSignals(True); self.act_alt_speed.setChecked(bool(state.get("use_alt_speed_limits", False))); self.act_alt_speed.blockSignals(False)

    def set_live_sorting(self, checked: bool):
        self.cfg["live_sorting"] = bool(checked); save_config(self.cfg)
        self.proxy.set_live_sorting(bool(checked))
        if checked:
            h = self.table.horizontalHeader(); self.proxy.sort(h.sortIndicatorSection(), h.sortIndicatorOrder())

    # ------------------------------------------------------------------
    # Filters/sidebar
    # ------------------------------------------------------------------
    def _tags(self, t): return self.proxy._tags(t)
    def _tracker_name(self, t): return self.proxy._tracker_name(t)

    def rebuild_sidebar(self):
        current_kind, current_value = self.proxy.filter_kind, self.proxy.filter_value
        self.sidebar.blockSignals(True); self.sidebar.clear()
        torrents = list(self.sync.torrents.values())

        def group(title):
            root = QTreeWidgetItem([title]); root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable); font = root.font(0); font.setBold(True); root.setFont(0, font); self.sidebar.addTopLevelItem(root); root.setExpanded(True); return root
        def child(parent, label, kind, value, count):
            item = QTreeWidgetItem([f"{label} ({count})"]); item.setData(0, Qt.ItemDataRole.UserRole, kind); item.setData(0, Qt.ItemDataRole.UserRole + 1, value); parent.addChild(item)
            if (kind, value) == (current_kind, current_value): self.sidebar.setCurrentItem(item)

        status = group("STATUS")
        labels = [("All", "all"), ("Downloading", "downloading"), ("Seeding", "seeding"), ("Completed", "completed"), ("Resumed", "resumed"), ("Stopped", "stopped"), ("Active", "active"), ("Inactive", "inactive"), ("Stalled", "stalled"), ("Checking", "checking"), ("Errored", "errored")]
        for label, key in labels: child(status, label, "status", key, sum(1 for t in torrents if self.proxy.status_match(t, key)))

        cats = group("CATEGORIES"); child(cats, "All", "category", "*", len(torrents)); child(cats, "Uncategorized", "category", "", sum(1 for t in torrents if not str(t.get("category", "") or "")))
        for name in sorted(self.sync.categories, key=str.casefold): child(cats, name, "category", name, sum(1 for t in torrents if str(t.get("category", "") or "") == name))

        tags = group("TAGS"); child(tags, "All", "tag", "*", len(torrents)); child(tags, "Untagged", "tag", "", sum(1 for t in torrents if not self._tags(t)))
        for tag in sorted(self.sync.tags, key=str.casefold): child(tags, tag, "tag", tag, sum(1 for t in torrents if tag in self._tags(t)))

        trackers = group("TRACKERS"); child(trackers, "All", "tracker", "*", len(torrents)); child(trackers, "Trackerless", "tracker", "Trackerless", sum(1 for t in torrents if self._tracker_name(t) == "Trackerless"))
        for tracker in sorted(self.sync.trackers, key=str.casefold):
            try:
                from urllib.parse import urlparse
                label = urlparse(tracker).hostname or tracker
            except Exception:
                label = tracker
            child(trackers, label, "tracker", label, len(self.sync.trackers.get(tracker, [])))
        self.sidebar.blockSignals(False)

    def sidebar_selection_changed(self):
        items = self.sidebar.selectedItems()
        if not items: return
        item = items[0]; kind = item.data(0, Qt.ItemDataRole.UserRole); value = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if kind is not None:
            selected = self.selected_hashes(); v = self.table.verticalScrollBar().value()
            self.proxy.set_filter(str(kind), str(value)); self.restore_selection(selected); self.table.verticalScrollBar().setValue(v); self.update_statusbar()

    def sidebar_context_menu(self, pos: QPoint):
        item = self.sidebar.itemAt(pos); menu = QMenu(self)
        if not item:
            return
        kind = item.data(0, Qt.ItemDataRole.UserRole); value = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if kind == "category":
            create = menu.addAction("Add category…")
            edit = menu.addAction("Edit category…") if value not in {None, "", "*"} else None
            remove = menu.addAction("Remove category") if value not in {None, "", "*"} else None
            chosen = menu.exec(self.sidebar.viewport().mapToGlobal(pos))
            if chosen == create: self.create_category()
            elif edit and chosen == edit: self.edit_category(str(value))
            elif remove and chosen == remove: self.remove_category(str(value))
        elif kind == "tag":
            create = menu.addAction("Add tag…")
            remove = menu.addAction("Delete tag") if value not in {None, "", "*"} else None
            chosen = menu.exec(self.sidebar.viewport().mapToGlobal(pos))
            if chosen == create: self.create_tag()
            elif remove and chosen == remove: self.delete_tag(str(value))

    def search_changed(self, text: str):
        selected = self.selected_hashes(); v = self.table.verticalScrollBar().value(); self.proxy.set_search(text); self.restore_selection(selected); self.table.verticalScrollBar().setValue(v); self.update_statusbar()

    # ------------------------------------------------------------------
    # Selection helpers / properties
    # ------------------------------------------------------------------
    def selected_hashes(self) -> list[str]:
        hashes: list[str] = []
        for idx in self.table.selectionModel().selectedRows():
            h = str(idx.data(HASH_ROLE) or "")
            if h and h not in hashes: hashes.append(h)
        return hashes

    def restore_selection(self, hashes: list[str]):
        if not hashes: return
        selection = self.table.selectionModel(); selection.clearSelection()
        wanted = set(hashes)
        for row in range(self.proxy.rowCount()):
            idx = self.proxy.index(row, 0)
            if str(idx.data(HASH_ROLE) or "") in wanted:
                selection.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def selected_torrent(self) -> dict[str, Any] | None:
        hashes = self.selected_hashes()
        return self.sync.torrents.get(hashes[0]) if len(hashes) == 1 else None

    def selection_changed(self):
        self.update_context_checks()
        self.update_action_states()
        QTimer.singleShot(160, self.refresh_selected_details)

    def update_action_states(self):
        hashes = self.selected_hashes() if hasattr(self, "table") else []
        selected = [self.sync.torrents.get(h, {}) for h in hashes]
        any_sel = bool(hashes)
        one = len(hashes) == 1
        all_complete = bool(selected) and all(float(t.get("progress", 0) or 0) >= .999 for t in selected)
        all_incomplete = bool(selected) and all(float(t.get("progress", 0) or 0) < .999 for t in selected)
        for action in [
            self.act_start, self.act_stop, self.act_remove, self.act_q_top, self.act_q_up,
            self.act_q_down, self.act_q_bottom, self.act_recheck, self.act_reannounce,
            self.act_force_start, self.act_auto_tmm, self.act_location, self.act_save_path,
            self.act_download_path, self.act_torrent_limits, self.act_share_limits,
        ]:
            action.setEnabled(any_sel)
        for action in [self.act_open_folder, self.act_rename, self.act_comment, self.act_export]:
            action.setEnabled(one)
        self.act_super_seed.setEnabled(all_complete)
        self.act_sequential.setEnabled(all_incomplete)
        self.act_first_last.setEnabled(all_incomplete)

    def update_context_checks(self):
        t = self.selected_torrent()
        actions = [self.act_force_start, self.act_super_seed, self.act_sequential, self.act_first_last, self.act_auto_tmm]
        for a in actions: a.blockSignals(True)
        if t:
            self.act_force_start.setChecked(bool(t.get("force_start", False)))
            self.act_super_seed.setChecked(bool(t.get("super_seeding", False)))
            self.act_sequential.setChecked(bool(t.get("seq_dl", False)))
            self.act_first_last.setChecked(bool(t.get("f_l_piece_prio", False)))
            self.act_auto_tmm.setChecked(bool(t.get("auto_tmm", False)))
        else:
            for a in actions: a.setChecked(False)
        for a in actions: a.blockSignals(False)

    def refresh_selected_details(self):
        if self.detail_busy or not self.tabs.isVisible(): return
        t = self.selected_torrent()
        if not t:
            self.clear_details(); return
        h = str(t.get("hash", ""))
        if self.last_detail_hash != h:
            self.last_detail_hash = h
            self.last_detail_data = {}
            self.clear_details()
        tab = self.tabs.currentIndex()
        kind, fn = {
            0: ("properties", lambda: self.client().torrent_properties(h)),
            1: ("trackers", lambda: self.client().torrent_trackers(h)),
            2: ("peers", lambda: self.client().torrent_peers(h)),
            3: ("webseeds", lambda: self.client().torrent_webseeds(h)),
            4: ("files", lambda: self.client().torrent_files(h)),
        }.get(tab, ("properties", lambda: self.client().torrent_properties(h)))
        self.detail_busy = True
        def done(value):
            if self.last_detail_hash == h:
                self.last_detail_data[kind] = value
                self.populate_details(t, self.last_detail_data)
        self.run_worker(fn, done, lambda m: log.warning("Details: %s", m), lambda: setattr(self, "detail_busy", False))

    def clear_details(self):
        for table in (self.general, self.trackers, self.peers, self.webseeds, self.content): table.setRowCount(0)
        self.last_detail_data = {}

    def populate_details(self, t: dict[str, Any], data: dict[str, Any]):
        p = data.get("properties", {}) or {}
        general = [
            ("Name", t.get("name", "")), ("Save path", t.get("save_path", "")), ("Download path", t.get("download_path", "")), ("Content path", t.get("content_path", "")),
            ("Total size", human_bytes(t.get("total_size", t.get("size", 0)))), ("Progress", f"{float(t.get('progress',0) or 0)*100:.2f}%"),
            ("Downloaded", human_bytes(t.get("downloaded", 0))), ("Uploaded", human_bytes(t.get("uploaded", 0))), ("Wasted", human_bytes(t.get("total_wasted", 0))),
            ("Download speed", human_speed(t.get("dlspeed", 0))), ("Upload speed", human_speed(t.get("upspeed", 0))), ("Ratio", f"{float(t.get('ratio',0) or 0):.3f}"),
            ("ETA", human_eta(t.get("eta", -1))), ("Added on", format_timestamp(t.get("added_on", 0))), ("Completed on", format_timestamp(t.get("completion_on", 0))),
            ("Time active", human_eta(p.get("time_elapsed", 0))), ("Seeding time", human_eta(p.get("seeding_time", 0))),
            ("Connections", f"{p.get('nb_connections', t.get('connections_count',0))} / {p.get('nb_connections_limit', t.get('connections_limit',0))}"),
            ("Seeds", f"{p.get('seeds', t.get('num_seeds',0))} ({p.get('seeds_total', t.get('num_complete',0))})"),
            ("Peers", f"{p.get('peers', t.get('num_leechs',0))} ({p.get('peers_total', t.get('num_incomplete',0))})"),
            ("Availability", t.get("availability", "")), ("Created by", p.get("created_by", t.get("created_by", ""))),
            ("Info hash", t.get("hash", "")), ("Private", p.get("private", "")), ("Comment", p.get("comment", t.get("comment", ""))),
        ]
        self.general.setRowCount(len(general))
        for r, (k, v) in enumerate(general): self.general.setItem(r, 0, QTableWidgetItem(str(k))); self.general.setItem(r, 1, QTableWidgetItem(str(v)))

        trackers = data.get("trackers", []) or []; self.trackers.setRowCount(len(trackers)); status_names = {0:"Disabled",1:"Not contacted",2:"Working",3:"Updating",4:"Not working"}
        for r, x in enumerate(trackers):
            vals = [x.get("url", ""), status_names.get(x.get("status"), str(x.get("status", ""))), x.get("num_seeds", 0), x.get("num_leeches", 0), x.get("msg", "")]
            for c, v in enumerate(vals): self.trackers.setItem(r, c, QTableWidgetItem(str(v)))

        peers = list((data.get("peers", {}) or {}).values()); self.peers.setRowCount(len(peers))
        for r, x in enumerate(peers):
            vals = [x.get("ip", ""), x.get("port", ""), x.get("client", ""), f"{float(x.get('progress',0) or 0)*100:.1f}%", human_speed(x.get("dl_speed", 0)), human_speed(x.get("up_speed", 0)), x.get("flags", "")]
            for c, v in enumerate(vals): self.peers.setItem(r, c, QTableWidgetItem(str(v)))

        webseeds = data.get("webseeds", []) or []; self.webseeds.setRowCount(len(webseeds))
        for r, url in enumerate(webseeds): self.webseeds.setItem(r, 0, QTableWidgetItem(str(url)))

        files = data.get("files", []) or []; self.content.setRowCount(len(files)); pri_names = {0:"Do not download",1:"Normal",6:"High",7:"Maximum"}
        for r, x in enumerate(files):
            vals = [x.get("index", r), x.get("name", ""), human_bytes(x.get("size", 0)), f"{float(x.get('progress',0) or 0)*100:.1f}%", pri_names.get(x.get("priority"), str(x.get("priority", "")))]
            for c, v in enumerate(vals): self.content.setItem(r, c, QTableWidgetItem(str(v)))

    # ------------------------------------------------------------------
    # Add torrent / shell-handled sources
    # ------------------------------------------------------------------
    def categories(self) -> list[str]: return list(self.sync.categories.keys())
    def tags(self) -> list[str]: return list(self.sync.tags)

    def show_add_dialog(self, sources: list[str]):
        # Read the remote qBittorrent add defaults first so the dialog
        # behaves like the native Add New Torrent dialog rather than inventing
        # a second set of defaults on the laptop.
        self.status_msg.setText(" Reading Add Torrent defaults from remote host… ")

        def open_dialog(prefs):
            dialog = AddTorrentDialog(
                self.client(), sources, self.cfg.get("save_path", DEFAULT_SAVE_PATH),
                self.categories(), self.tags(), self, prefs=prefs or {},
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.status_msg.setText(" Add torrent canceled. ")
                return
            items = dialog.source_list(); options = dialog.options()
            self.status_msg.setText(" Sending torrent(s) to remote host… ")

            def done(results):
                ok = [x for x in results if x.get("ok")]; bad = [x for x in results if not x.get("ok")]
                if bad:
                    QMessageBox.warning(self, "Some torrents failed", f"{len(ok)} accepted, {len(bad)} failed.\n\n" + "\n\n".join(f"{x['item']}\n{x['detail']}" for x in bad[:8]))
                self.status_msg.setText(f" {len(ok)} torrent(s) accepted · {len(bad)} failed ")
                QTimer.singleShot(500, lambda: self.refresh_sync(full=True))

            self.run_worker(lambda: self.client().add_sources(items, options), done, self.show_error)

        self.run_worker(lambda: self.client().preferences(), open_dialog, self.show_error)

    def add_torrent_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add Torrent Files", str(Path.home() / "Downloads"), "Torrent files (*.torrent);;All files (*.*)")
        if files: self.show_add_dialog(files)

    def add_torrent_links(self):
        initial = QApplication.clipboard().text().strip(); text, ok = _multiline_text_dialog(self, "Add Torrent Link", "Enter one or more torrent URLs or magnet links:", initial if ("magnet:?" in initial or "http" in initial) else "")
        if ok:
            sources = QbtClient.parse_sources(text)
            if sources: self.show_add_dialog(sources)

    def consume_launch_sources(self):
        if not self.pending_launch_sources: return
        sources = self.pending_launch_sources[:]; self.pending_launch_sources.clear(); self.showNormal(); self.raise_(); self.activateWindow(); self.show_add_dialog(sources)

    def accept_external_sources(self, sources: list[str]):
        self.pending_launch_sources.extend(sources); QTimer.singleShot(50, self.consume_launch_sources)

    # ------------------------------------------------------------------
    # Core torrent actions
    # ------------------------------------------------------------------
    def _selected_or_notice(self) -> list[str]:
        hashes = self.selected_hashes()
        if not hashes: self.status_msg.setText(" Select one or more torrents first. ")
        return hashes

    def _after_action(self, _=None): QTimer.singleShot(250, self.refresh_sync)

    def basic_action(self, action: str):
        hashes = self._selected_or_notice()
        if not hashes: return
        c = self.client(); fn = {"start": lambda:c.start(hashes), "stop": lambda:c.stop(hashes), "recheck": lambda:c.recheck(hashes), "reannounce": lambda:c.reannounce(hashes)}[action]
        self.run_worker(fn, self._after_action, self.show_error)

    def session_action(self, action: str):
        fn = self.client().start if action == "start" else self.client().stop
        label = "resume" if action == "start" else "pause"
        self.status_msg.setText(f" Requesting session {label}… ")
        self.run_worker(lambda: fn(["all"]), self._after_action, self.show_error)

    def queue_action(self, where: str):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().queue(hashes, where), self._after_action, self.show_error)

    def toggle_force_start(self, checked: bool):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().set_force_start(hashes, checked), self._after_action, self.show_error)

    def toggle_super_seeding(self, checked: bool):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().set_super_seeding(hashes, checked), self._after_action, self.show_error)

    def toggle_sequential(self, _checked=False):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().toggle_sequential(hashes), self._after_action, self.show_error)

    def toggle_first_last(self, _checked=False):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().toggle_first_last(hashes), self._after_action, self.show_error)

    def toggle_auto_tmm(self, checked: bool):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().set_auto_management(hashes, checked), self._after_action, self.show_error)

    def rename_selected(self):
        t = self.selected_torrent()
        if not t: self.status_msg.setText(" Select exactly one torrent to rename. "); return
        text, ok = single_text_dialog(self, "Rename Torrent", "New torrent name:", str(t.get("name", "")))
        if ok and text.strip(): self.run_worker(lambda:self.client().rename(str(t.get("hash")), text.strip()), self._after_action, self.show_error)

    def comment_selected(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        initial = str(self.selected_torrent().get("comment", "")) if len(hashes) == 1 and self.selected_torrent() else ""
        text, ok = _multiline_text_dialog(self, "Set Comment", "Comment for selected torrent(s):", initial)
        if ok: self.run_worker(lambda:self.client().set_comment(hashes, text), self._after_action, self.show_error)

    def choose_remote_path(self, initial: str) -> str | None:
        d = RemotePathDialog(self.client(), initial or self.cfg.get("save_path", DEFAULT_SAVE_PATH), self)
        return d.selected_path if d.exec() == QDialog.DialogCode.Accepted else None

    def set_location(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        initial = str(self.selected_torrent().get("save_path", "")) if self.selected_torrent() else self.cfg.get("save_path", DEFAULT_SAVE_PATH)
        path = self.choose_remote_path(initial)
        if path: self.run_worker(lambda:self.client().set_location(hashes, path), self._after_action, self.show_error)

    def set_save_path(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        initial = str(self.selected_torrent().get("save_path", "")) if self.selected_torrent() else self.cfg.get("save_path", DEFAULT_SAVE_PATH)
        path = self.choose_remote_path(initial)
        if path: self.run_worker(lambda:self.client().set_save_path(hashes, path), self._after_action, self.show_error)

    def set_download_path(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        initial = str(self.selected_torrent().get("download_path", "")) if self.selected_torrent() else self.cfg.get("save_path", DEFAULT_SAVE_PATH)
        path = self.choose_remote_path(initial or self.cfg.get("save_path", DEFAULT_SAVE_PATH))
        if path: self.run_worker(lambda:self.client().set_download_path(hashes, path), self._after_action, self.show_error)

    def set_torrent_limits(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        d = TorrentLimitsDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            dl, up = d.limits(); c = self.client()
            self.run_worker(lambda:(c.set_download_limit(hashes, dl), c.set_upload_limit(hashes, up)), self._after_action, self.show_error)

    def set_share_limits(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        d = ShareLimitsDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            ratio, seed, inactive, action = d.values()
            self.run_worker(lambda:self.client().set_share_limits(hashes, ratio, seed, inactive, action), self._after_action, self.show_error)

    def delete_selected(self):
        hashes = self._selected_or_notice()
        if not hashes: return
        box = QMessageBox(self); box.setWindowTitle("Remove Torrent"); box.setIcon(QMessageBox.Icon.Warning); box.setText(f"Remove {len(hashes)} selected torrent(s)?"); box.setInformativeText("Choose whether the downloaded files on the remote host should also be deleted.")
        keep = box.addButton("Remove torrent, keep files", QMessageBox.ButtonRole.AcceptRole); delete_files = box.addButton("Delete torrent + files", QMessageBox.ButtonRole.DestructiveRole); cancel = box.addButton(QMessageBox.StandardButton.Cancel); box.setDefaultButton(keep); box.exec()
        if box.clickedButton() == cancel: return
        self.run_worker(lambda:self.client().delete(hashes, box.clickedButton() == delete_files), self._after_action, self.show_error)

    # ------------------------------------------------------------------
    # Category/tag controls
    # ------------------------------------------------------------------
    def set_selected_category(self, category: str):
        hashes = self._selected_or_notice()
        if hashes: self.run_worker(lambda:self.client().set_category(hashes, category), self._after_action, self.show_error)

    def manage_tags(self, mode: str):
        hashes = self._selected_or_notice()
        if not hashes: return
        text, ok = single_text_dialog(self, f"{mode.title()} Tags", "Comma-separated tags:", "")
        if not ok: return
        tags = [x.strip() for x in text.split(",") if x.strip()]
        if not tags and mode != "set": return
        c = self.client(); fn = {"add": lambda:c.add_tags(hashes, tags), "remove": lambda:c.remove_tags(hashes, tags), "set": lambda:c.set_tags(hashes, tags)}[mode]
        self.run_worker(fn, self._after_action, self.show_error)

    def create_category(self):
        name, ok = single_text_dialog(self, "Add Category", "Category name:")
        if not ok or not name.strip(): return
        path = self.choose_remote_path(self.cfg.get("save_path", DEFAULT_SAVE_PATH))
        if path is None: path = ""
        self.run_worker(lambda:self.client().create_category(name.strip(), path), self._after_action, self.show_error)

    def edit_category(self, name: str):
        current = self.sync.categories.get(name, {}).get("savePath", "")
        path = self.choose_remote_path(str(current or self.cfg.get("save_path", DEFAULT_SAVE_PATH)))
        if path: self.run_worker(lambda:self.client().edit_category(name, path), self._after_action, self.show_error)

    def remove_category(self, name: str):
        if QMessageBox.question(self, APP_NAME, f"Remove category '{name}'?") == QMessageBox.StandardButton.Yes:
            self.run_worker(lambda:self.client().remove_categories([name]), self._after_action, self.show_error)

    def create_tag(self):
        text, ok = single_text_dialog(self, "Add Tag", "Tag name:")
        if ok and text.strip(): self.run_worker(lambda:self.client().create_tags([text.strip()]), self._after_action, self.show_error)

    def delete_tag(self, tag: str):
        if QMessageBox.question(self, APP_NAME, f"Delete tag '{tag}'?") == QMessageBox.StandardButton.Yes:
            self.run_worker(lambda:self.client().delete_tags([tag]), self._after_action, self.show_error)

    # ------------------------------------------------------------------
    # Trackers / peers / HTTP sources / content priorities
    # ------------------------------------------------------------------
    def trackers_menu(self, pos: QPoint):
        t = self.selected_torrent()
        if not t: return
        m = QMenu(self); add = m.addAction("Add tracker…"); edit = m.addAction("Edit tracker…"); remove = m.addAction("Remove tracker"); m.addSeparator(); reannounce = m.addAction(self.act_reannounce)
        chosen = m.exec(self.trackers.viewport().mapToGlobal(pos)); h = str(t.get("hash"))
        if chosen == add:
            text, ok = _multiline_text_dialog(self, "Add Trackers", "Tracker URL(s), one per line:")
            if ok and text.strip(): self.run_worker(lambda:self.client().add_trackers(h, [x.strip() for x in text.splitlines() if x.strip()]), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen == edit:
            row = self.trackers.currentRow(); old = self.trackers.item(row,0).text() if row >= 0 and self.trackers.item(row,0) else ""
            if old.startswith("** "): return
            text, ok = single_text_dialog(self, "Edit Tracker", "Tracker URL:", old)
            if ok and text.strip(): self.run_worker(lambda:self.client().edit_tracker(h, old, text.strip()), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen == remove:
            rows = sorted({x.row() for x in self.trackers.selectionModel().selectedRows()}); urls = [self.trackers.item(r,0).text() for r in rows if self.trackers.item(r,0) and not self.trackers.item(r,0).text().startswith("** ")]
            if urls: self.run_worker(lambda:self.client().remove_trackers(h, urls), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen == reannounce: self.basic_action("reannounce")

    def peers_menu(self, pos: QPoint):
        t = self.selected_torrent()
        if not t: return
        m = QMenu(self); add = m.addAction("Add Peer…"); ban = m.addAction("Ban Selected Peer(s)"); chosen = m.exec(self.peers.viewport().mapToGlobal(pos))
        if chosen == add:
            text, ok = single_text_dialog(self, "Add Peer", "Peer (IP:port):")
            if ok and text.strip(): self.run_worker(lambda:self.client().add_peers([str(t.get("hash"))], [text.strip()]), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen == ban:
            peers = []
            for idx in self.peers.selectionModel().selectedRows():
                ip = self.peers.item(idx.row(),0); port = self.peers.item(idx.row(),1)
                if ip and port: peers.append(f"{ip.text()}:{port.text()}")
            if peers and QMessageBox.question(self, APP_NAME, f"Ban {len(peers)} selected peer(s) globally?") == QMessageBox.StandardButton.Yes:
                self.run_worker(lambda:self.client().ban_peers(peers), lambda _:self.refresh_selected_details(), self.show_error)

    def webseeds_menu(self, pos: QPoint):
        t = self.selected_torrent()
        if not t: return
        m = QMenu(self); add = m.addAction("Add URL seed…"); edit = m.addAction("Edit URL seed…"); remove = m.addAction("Remove URL seed"); chosen = m.exec(self.webseeds.viewport().mapToGlobal(pos)); h = str(t.get("hash"))
        if chosen == add:
            text, ok = _multiline_text_dialog(self, "Add URL Seeds", "HTTP/HTTPS/FTP source URL(s):")
            if ok and text.strip(): self.run_worker(lambda:self.client().add_webseeds(h, [x.strip() for x in text.splitlines() if x.strip()]), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen == edit:
            row = self.webseeds.currentRow(); old = self.webseeds.item(row,0).text() if row >= 0 and self.webseeds.item(row,0) else ""
            text, ok = single_text_dialog(self, "Edit URL Seed", "URL:", old)
            if ok and text.strip(): self.run_worker(lambda:self.client().edit_webseed(h, old, text.strip()), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen == remove:
            urls = [self.webseeds.item(i.row(),0).text() for i in self.webseeds.selectionModel().selectedRows() if self.webseeds.item(i.row(),0)]
            if urls: self.run_worker(lambda:self.client().remove_webseeds(h, urls), lambda _:self.refresh_selected_details(), self.show_error)

    def content_menu(self, pos: QPoint):
        t = self.selected_torrent()
        if not t: return
        rows = sorted({x.row() for x in self.content.selectionModel().selectedRows()}); ids = []
        for row in rows:
            item = self.content.item(row,0)
            if item:
                try: ids.append(int(item.text()))
                except ValueError: pass
        if not ids: return
        m = QMenu(self)
        rename_file = m.addAction("Rename file…") if len(rows) == 1 else None
        if rename_file: m.addSeparator()
        choices = [("Do not download",0),("Normal",1),("High",6),("Maximum",7)]
        acts = {m.addAction(label):prio for label,prio in choices}
        chosen = m.exec(self.content.viewport().mapToGlobal(pos))
        if rename_file and chosen == rename_file:
            row = rows[0]
            name_item = self.content.item(row, 1)
            if not name_item: return
            old_path = name_item.text()
            new_path, ok = single_text_dialog(self, "Rename file", "New path/name:", old_path)
            if ok and new_path.strip() and new_path.strip() != old_path:
                self.run_worker(lambda:self.client().rename_file(str(t.get("hash")), old_path, new_path.strip()), lambda _:self.refresh_selected_details(), self.show_error)
        elif chosen in acts:
            self.run_worker(lambda:self.client().file_priority(str(t.get("hash")), ids, acts[chosen]), lambda _:self.refresh_selected_details(), self.show_error)

    # ------------------------------------------------------------------
    # Main transfer-list context menu
    # ------------------------------------------------------------------
    def open_table_menu(self, pos: QPoint):
        idx = self.table.indexAt(pos)
        if idx.isValid() and not self.table.selectionModel().isRowSelected(idx.row(), QModelIndex()):
            self.table.selectionModel().clearSelection()
            self.table.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            self.table.setCurrentIndex(idx)
        self.update_context_checks(); self.update_action_states(); m = QMenu(self)
        m.addAction(self.act_start); m.addAction(self.act_stop); m.addAction(self.act_force_start); m.addSeparator(); m.addAction(self.act_open_folder)
        q = m.addMenu("Queue"); q.addAction(self.act_q_top); q.addAction(self.act_q_up); q.addAction(self.act_q_down); q.addAction(self.act_q_bottom)
        m.addSeparator(); m.addAction(self.act_recheck); m.addAction(self.act_reannounce)
        advanced = m.addMenu("Torrent options"); advanced.addAction(self.act_super_seed); advanced.addAction(self.act_sequential); advanced.addAction(self.act_first_last); advanced.addAction(self.act_auto_tmm); advanced.addSeparator(); advanced.addAction(self.act_torrent_limits); advanced.addAction(self.act_share_limits)
        category = m.addMenu("Category"); unc = category.addAction("Uncategorized"); unc.triggered.connect(lambda:self.set_selected_category(""))
        if self.sync.categories: category.addSeparator()
        for name in sorted(self.sync.categories, key=str.casefold):
            a = category.addAction(name); a.triggered.connect(lambda _=False, n=name:self.set_selected_category(n))
        tags = m.addMenu("Tags"); tags.addAction("Add tags…", lambda:self.manage_tags("add")); tags.addAction("Remove tags…", lambda:self.manage_tags("remove")); tags.addAction("Replace tags…", lambda:self.manage_tags("set"))
        m.addSeparator(); m.addAction(self.act_location); paths = m.addMenu("Advanced paths"); paths.addAction(self.act_save_path); paths.addAction(self.act_download_path)
        m.addSeparator(); m.addAction(self.act_rename); m.addAction(self.act_comment); m.addAction(self.act_export); m.addSeparator(); m.addAction(self.act_remove)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def export_selected(self):
        t = self.selected_torrent()
        if not t:
            self.status_msg.setText(" Select exactly one torrent to export. ")
            return
        name = re.sub(r'[<>:"/\\|?*]+', '_', str(t.get("name", "torrent"))).strip() or "torrent"
        path, _ = QFileDialog.getSaveFileName(self, "Export .torrent", str(Path.home() / "Downloads" / f"{name}.torrent"), "Torrent files (*.torrent)")
        if not path:
            return
        h = str(t.get("hash", ""))
        def save_bytes(blob: bytes):
            try:
                Path(path).write_bytes(blob)
                self.status_msg.setText(f" Exported {Path(path).name}. ")
            except Exception as e:
                self.show_error(str(e))
        self.run_worker(lambda: self.client().export_torrent(h), save_bytes, self.show_error)

    # ------------------------------------------------------------------
    # Global controls, logging, statistics
    # ------------------------------------------------------------------
    def set_global_limits(self):
        def got(values):
            d = SpeedLimitsDialog(values, self)
            if d.exec() == QDialog.DialogCode.Accepted:
                v = d.values(); self.run_worker(lambda:self.client().set_speed_limits(v["up_limit"], v["dl_limit"], v["alt_up_limit"], v["alt_dl_limit"]), self._after_action, self.show_error)
        self.run_worker(lambda:self.client().speed_limits(), got, self.show_error)

    def toggle_alt_speed(self, checked: bool):
        self.run_worker(lambda:self.client().set_speed_limits_mode(checked), self._after_action, self.show_error)

    def show_statistics(self):
        s = self.sync.server_state
        text = "\n".join([
            f"qBittorrent: {self.version or '—'}", f"Web API: {self.webapi_version or '—'}", "",
            f"Session downloaded: {human_bytes(s.get('dl_info_data',0))}", f"Session uploaded: {human_bytes(s.get('up_info_data',0))}",
            f"All-time downloaded: {human_bytes(s.get('alltime_dl',0))}", f"All-time uploaded: {human_bytes(s.get('alltime_ul',0))}",
            f"Global ratio: {s.get('global_ratio','—')}", f"Peer connections: {s.get('total_peer_connections','—')}",
            f"DHT nodes: {s.get('dht_nodes','—')}", f"Free space: {human_bytes(s.get('free_space_on_disk',-1))}",
            f"Queued I/O jobs: {s.get('queued_io_jobs','—')}", f"Wasted this session: {human_bytes(s.get('total_wasted_session',0))}",
        ])
        QMessageBox.information(self, "qBittorrent Statistics — Remote Host", text)

    def show_log(self):
        def got(rows):
            d = QDialog(self); d.setWindowTitle("Execution Log — Remote Host"); d.resize(900, 540); layout = QVBoxLayout(d)
            table = QTableWidget(0,3); table.setHorizontalHeaderLabels(["Time","Type","Message"]); table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch); table.verticalHeader().setVisible(False)
            table.setRowCount(len(rows)); types={1:"Normal",2:"Info",4:"Warning",8:"Critical"}
            for r,x in enumerate(rows):
                vals=[format_timestamp(x.get("timestamp",0)),types.get(x.get("type"),str(x.get("type",""))),x.get("message","")]
                for c,v in enumerate(vals): table.setItem(r,c,QTableWidgetItem(str(v)))
            layout.addWidget(table); close=QPushButton("Close"); close.clicked.connect(d.accept); layout.addWidget(close,0,Qt.AlignmentFlag.AlignRight); d.exec()
        self.run_worker(lambda:self.client().main_log(-1), got, self.show_error)

    def shutdown_remote(self):
        if QMessageBox.warning(self, "Exit Remote qBittorrent", "This will close qBittorrent on the remote host and stop all downloading/seeding until it is started again.\n\nContinue?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.run_worker(lambda:self.client().shutdown(), lambda _:self.status_msg.setText(" Remote qBittorrent was told to exit. "), self.show_error)

    # ------------------------------------------------------------------
    # Files, settings, Windows integration
    # ------------------------------------------------------------------
    def open_selected_folder(self):
        t = self.selected_torrent(); path = self.cfg.get("smb_path", DEFAULT_SMB_PATH)
        if t:
            remote = str(t.get("content_path", "") or t.get("save_path", "") or "")
            base_remote = str(self.cfg.get("save_path", DEFAULT_SAVE_PATH)).rstrip("\\/")
            base_smb = str(self.cfg.get("smb_path", DEFAULT_SMB_PATH)).rstrip("\\/")
            if remote and remote.lower().startswith(base_remote.lower()):
                rel = remote[len(base_remote):].lstrip("\\/"); path = base_smb + ("\\" + rel if rel else "")
            elif re.match(r"^[A-Za-z]:\\", remote):
                drive = remote[0].upper(); rest = remote[3:]; host = self.remote_name(); path = rf"\\{host}\{drive}\{rest}"
        try:
            if os.name == "nt": os.startfile(path)
            else: QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Could not open:\n{path}\n\n{e}")

    def open_webui(self): QDesktopServices.openUrl(QUrl(self.cfg.get("server", DEFAULT_SERVER)))

    def open_qbt_options(self):
        def got(prefs):
            d = QbtPreferencesDialog(self.client(), prefs, self)
            if d.exec() == QDialog.DialogCode.Accepted:
                updates = d.updates()
                if updates.get("save_path"):
                    self.cfg["save_path"] = updates["save_path"]
                    save_config(self.cfg)
                    self.client_obj = QbtClient(self.cfg)
                self.run_worker(lambda: self.client().set_preferences(updates), self._after_action, self.show_error)
        self.run_worker(lambda: self.client().preferences(), got, self.show_error)

    def open_settings(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec() == QDialog.DialogCode.Accepted and d.result_cfg:
            old_integrate = bool(self.cfg.get("integrate_windows", True)); self.cfg = d.result_cfg; self.client_obj = QbtClient(self.cfg); self.sync.reset(); self.apply_refresh_interval(); self.act_live_sort.setChecked(bool(self.cfg.get("live_sorting", False))); self.proxy.set_live_sorting(bool(self.cfg.get("live_sorting", False)))
            new_integrate = bool(self.cfg.get("integrate_windows", True))
            if new_integrate != old_integrate:
                try: register_associations() if new_integrate else unregister_associations()
                except Exception as e: QMessageBox.warning(self, APP_NAME, f"Could not change Windows associations:\n\n{e}")
            self.refresh_sync(full=True)

    def register_windows(self):
        try:
            ok, msg = register_associations(); QMessageBox.information(self, APP_NAME, msg)
            if ok: self.cfg["integrate_windows"] = True; save_config(self.cfg)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Could not register Windows integration:\n\n{e}")

    # ------------------------------------------------------------------
    # RemoteQBT self-update (GitHub Releases)
    # ------------------------------------------------------------------
    def check_updates(self, manual: bool = False):
        if self.update_busy:
            return
        self.update_busy = True
        if manual:
            self.status_msg.setText(" Checking GitHub for RemoteQBT updates… ")

        def done(info):
            self.update_busy = False
            if info is None:
                if manual:
                    QMessageBox.information(self, APP_NAME, f"RemoteQBT {DISPLAY_VERSION} is up to date.")
                return
            self.pending_update = info
            self.act_update.setText(f"Update to RemoteQBT {info.release_id}…")
            self.status_update.setText(f' <a href="install" style="color:#72B4F5;">Update {info.release_id}</a> ')
            self.status_update.setToolTip(f"RemoteQBT {info.release_id} is available on GitHub")
            self.status_msg.setText(f" RemoteQBT {info.release_id} update available ")
            if manual:
                self.offer_update(info)

        def failed(message):
            self.update_busy = False
            log.warning("Update check: %s", message)
            if manual:
                QMessageBox.warning(self, APP_NAME, f"Could not check for RemoteQBT updates:\n\n{message}")

        self.run_worker(
            lambda: check_for_update(force=manual),
            done,
            failed,
            lambda: setattr(self, "update_busy", False),
        )

    def offer_update(self, info: UpdateInfo):
        notes = (info.notes or "").strip().replace("**", "")
        if len(notes) > 1200:
            notes = notes[:1200].rstrip() + "…"
        message = (
            f"RemoteQBT {info.release_id} is available.\n\n"
            + (notes + "\n\n" if notes else "")
            + "Download the verified Windows build and install it now?"
        )
        box = QMessageBox(self)
        box.setWindowTitle("RemoteQBT Update")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(message)
        install = box.addButton("Download && Install", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == install:
            self.install_pending_update()

    def install_pending_update(self):
        info = self.pending_update
        if info is None:
            self.check_updates(manual=True)
            return
        if self.update_busy:
            return
        self.update_busy = True
        self.status_update.setText(f" Downloading {info.release_id}… ")
        self.status_msg.setText(" Downloading and verifying RemoteQBT update… ")

        progress = QProgressDialog(
            f"Downloading and verifying RemoteQBT {info.release_id}…",
            None, 0, 0, self,
        )
        progress.setWindowTitle("RemoteQBT Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        progress.show()
        self.update_progress = progress
        QApplication.processEvents()

        def close_progress():
            if self.update_progress is not None:
                self.update_progress.close()
                self.update_progress.deleteLater()
                self.update_progress = None

        def ready(script):
            if self.update_progress is not None:
                self.update_progress.setLabelText(
                    "Download verified. Closing RemoteQBT and starting the Windows installer…"
                )
                QApplication.processEvents()
            self.status_msg.setText(" Download verified. Starting RemoteQBT installer… ")
            try:
                launch_installer(Path(script), info.release_id)
            except Exception as e:
                close_progress()
                self.update_busy = False
                self.status_update.setText(f' <a href="install" style="color:#72B4F5;">Update {info.release_id}</a> ')
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    f"Could not launch updater:\n\n{e}\n\nUpdate log:\n{UPDATE_LOG_FILE}",
                )
                return
            QTimer.singleShot(250, QApplication.quit)

        def failed(message):
            close_progress()
            self.update_busy = False
            self.status_update.setText(f' <a href="install" style="color:#72B4F5;">Update {info.release_id}</a> ')
            QMessageBox.warning(
                self,
                APP_NAME,
                f"RemoteQBT update failed:\n\n{message}\n\nUpdate log:\n{UPDATE_LOG_FILE}",
            )

        self.run_worker(lambda: download_update(info), ready, failed)

    def open_update_log(self):
        try:
            UPDATE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            if not UPDATE_LOG_FILE.exists():
                UPDATE_LOG_FILE.write_text(
                    "No RemoteQBT updater activity has been recorded yet.\n",
                    encoding="utf-8",
                )
            if os.name == "nt":
                os.startfile(str(UPDATE_LOG_FILE))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(UPDATE_LOG_FILE)))
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Could not open the update log:\n\n{e}")

    def toggle_sidebar(self, shown: bool):
        self.sidebar.setVisible(shown); self.cfg["show_sidebar"] = shown; save_config(self.cfg)

    def toggle_properties(self, shown: bool):
        self.tabs.setVisible(shown); self.cfg["show_properties"] = shown; save_config(self.cfg)

    def about(self):
        QMessageBox.about(
            self, "About RemoteQBT",
            f"<b>RemoteQBT {DISPLAY_VERSION}</b><br><br>qBittorrent-style remote desktop client.<br>"
            "The torrent engine, storage, and seeding remain on the remote host; RemoteQBT talks to qBittorrent through its Web API.<br><br>"
            "The transfer list uses qBittorrent's incremental sync API, so live updates do not rebuild or yank the table viewport.<br><br>"
            "RemoteQBT checks its GitHub Releases feed for verified Windows updates in the background.<br><br>"
            "<i>qBittorrent name, logo, and toolbar assets are from the qBittorrent project and used under the included GPL terms. RemoteQBT is an unofficial companion.</i>"
        )

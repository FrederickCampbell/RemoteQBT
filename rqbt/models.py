from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush

from .common import format_timestamp, human_bytes, human_eta, human_speed, state_color, state_label

HASH_ROLE = Qt.ItemDataRole.UserRole + 1
RAW_ROLE = Qt.ItemDataRole.UserRole + 2
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 3


COLUMNS = [
    ("priority", "#"),
    ("name", "Name"),
    ("size", "Size"),
    ("progress", "Progress"),
    ("state", "Status"),
    ("num_seeds", "Seeds"),
    ("num_leechs", "Peers"),
    ("dlspeed", "Down Speed"),
    ("upspeed", "Up Speed"),
    ("eta", "ETA"),
    ("ratio", "Ratio"),
    ("added_on", "Added On"),
]


class TorrentTableModel(QAbstractTableModel):
    """Stable in-place model. Refreshes update rows; they do not rebuild the table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.order: list[str] = []
        self.torrents: dict[str, dict[str, Any]] = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.order)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(COLUMNS):
            return COLUMNS[section][1]
        return section + 1

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def torrent_for_row(self, row: int) -> dict[str, Any] | None:
        if not (0 <= row < len(self.order)):
            return None
        return self.torrents.get(self.order[row])

    def hash_for_row(self, row: int) -> str:
        return self.order[row] if 0 <= row < len(self.order) else ""

    def row_for_hash(self, torrent_hash: str) -> int:
        try:
            return self.order.index(torrent_hash)
        except ValueError:
            return -1

    def _raw_value(self, t: dict[str, Any], key: str) -> Any:
        if key == "size":
            return int(t.get("size", t.get("total_size", 0)) or 0)
        if key == "progress":
            return float(t.get("progress", 0) or 0)
        if key == "state":
            return state_label(str(t.get("state", ""))).lower()
        if key in {"num_seeds", "num_leechs", "dlspeed", "upspeed", "eta", "priority", "added_on"}:
            try:
                return int(t.get(key, 0) or 0)
            except Exception:
                return 0
        if key == "ratio":
            try:
                return float(t.get("ratio", 0) or 0)
            except Exception:
                return 0.0
        return str(t.get(key, "") or "").lower()

    def _display_value(self, t: dict[str, Any], key: str) -> str:
        if key == "priority":
            p = int(t.get("priority", 0) or 0)
            return str(p) if p > 0 else ""
        if key == "name":
            return str(t.get("name", "") or "")
        if key == "size":
            return human_bytes(t.get("size", t.get("total_size", 0)))
        if key == "progress":
            return f"{float(t.get('progress', 0) or 0) * 100:.1f}%"
        if key == "state":
            return state_label(str(t.get("state", "")))
        if key == "num_seeds":
            return f"{int(t.get('num_seeds', 0) or 0)} ({int(t.get('num_complete', 0) or 0)})"
        if key == "num_leechs":
            return f"{int(t.get('num_leechs', 0) or 0)} ({int(t.get('num_incomplete', 0) or 0)})"
        if key == "dlspeed":
            return human_speed(t.get("dlspeed", 0))
        if key == "upspeed":
            return human_speed(t.get("upspeed", 0))
        if key == "eta":
            return human_eta(t.get("eta", -1))
        if key == "ratio":
            return f"{float(t.get('ratio', 0) or 0):.2f}"
        if key == "added_on":
            return format_timestamp(t.get("added_on", 0))
        return str(t.get(key, "") or "")

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.order)):
            return None
        h = self.order[index.row()]
        t = self.torrents.get(h, {})
        key = COLUMNS[index.column()][0]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(t, key)
        if role == HASH_ROLE:
            return h
        if role == RAW_ROLE:
            return self._raw_value(t, key)
        if role == PROGRESS_ROLE and key == "progress":
            return float(t.get("progress", 0) or 0) * 100
        if role == Qt.ItemDataRole.ForegroundRole and key == "state":
            return QBrush(state_color(str(t.get("state", ""))))
        if role == Qt.ItemDataRole.ToolTipRole:
            if key == "name":
                return str(t.get("name", "") or "")
            if key == "state":
                return str(t.get("state", "") or "")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if key not in {"name", "state"}:
                return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    def replace_snapshot(self, torrents: dict[str, dict[str, Any]], *, first_load_sort: bool = False):
        incoming = set(torrents)
        existing = set(self.order)
        changed_rows: list[int] = []

        # Remove deleted torrents from the end so row numbers remain valid.
        removed_rows = [i for i, h in enumerate(self.order) if h not in incoming]
        for row in reversed(removed_rows):
            self.beginRemoveRows(QModelIndex(), row, row)
            h = self.order.pop(row)
            self.torrents.pop(h, None)
            self.endRemoveRows()

        # Update only rows whose values actually changed. Store copies rather than
        # references to SyncState's dictionaries so later incremental patches can
        # be detected instead of mutating the model behind Qt's back.
        for row, h in enumerate(list(self.order)):
            if h in torrents:
                incoming_torrent = dict(torrents[h])
                if self.torrents.get(h) != incoming_torrent:
                    self.torrents[h] = incoming_torrent
                    changed_rows.append(row)

        # New torrents are appended. Sorting is an explicit view decision; this
        # prevents an arriving torrent from stealing the user's viewport.
        new_hashes = [h for h in torrents.keys() if h not in existing]
        if first_load_sort:
            new_hashes.sort(key=lambda h: (
                int(torrents[h].get("priority", 0) or 0) <= 0,
                int(torrents[h].get("priority", 0) or 0),
                -int(torrents[h].get("added_on", 0) or 0),
            ))
        if new_hashes:
            start = len(self.order)
            end = start + len(new_hashes) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self.order.extend(new_hashes)
            for h in new_hashes:
                self.torrents[h] = dict(torrents[h])
            self.endInsertRows()

        # Emit only changed rows, not the entire 62+ torrent table every poll.
        for row in changed_rows:
            self.dataChanged.emit(
                self.index(row, 0), self.index(row, len(COLUMNS) - 1),
                [Qt.ItemDataRole.DisplayRole, RAW_ROLE, PROGRESS_ROLE, Qt.ItemDataRole.ForegroundRole],
            )



class TorrentProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_kind = "status"
        self.filter_value = "all"
        self.search_text = ""
        self.setDynamicSortFilter(False)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._frozen_sort: dict[str, Any] = {}
        self._frozen_sort_column = -1

    @property
    def source(self) -> TorrentTableModel:
        return self.sourceModel()  # type: ignore[return-value]

    @staticmethod
    def _tags(t: dict[str, Any]) -> list[str]:
        return [x.strip() for x in str(t.get("tags", "") or "").split(",") if x.strip()]

    @staticmethod
    def _tracker_name(t: dict[str, Any]) -> str:
        from urllib.parse import urlparse
        tracker = str(t.get("tracker", "") or "")
        if not tracker:
            return "Trackerless"
        try:
            return urlparse(tracker).hostname or tracker
        except Exception:
            return tracker

    @staticmethod
    def status_match(t: dict[str, Any], key: str) -> bool:
        s = str(t.get("state", "") or "").lower()
        p = float(t.get("progress", 0) or 0)
        if key == "all": return True
        if key == "downloading": return ("dl" in s or "downloading" in s or "metadl" in s) and "stopped" not in s and "paused" not in s
        if key == "seeding": return ("up" in s or "upload" in s) and "stopped" not in s and "paused" not in s and p >= .999
        if key == "completed": return p >= .999
        if key == "resumed": return "paused" not in s and "stopped" not in s
        if key == "stopped": return "paused" in s or "stopped" in s
        if key == "active": return int(t.get("dlspeed", 0) or 0) > 0 or int(t.get("upspeed", 0) or 0) > 0
        if key == "inactive": return int(t.get("dlspeed", 0) or 0) == 0 and int(t.get("upspeed", 0) or 0) == 0
        if key == "stalled": return "stalled" in s
        if key == "checking": return "checking" in s
        if key == "errored": return "error" in s or "missing" in s
        return True

    def set_filter(self, kind: str, value: str):
        self.filter_kind = kind
        self.filter_value = value
        self.invalidateFilter()

    def set_search(self, text: str):
        self.search_text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        t = self.source.torrent_for_row(source_row) or {}
        if self.search_text and self.search_text not in str(t.get("name", "") or "").lower():
            return False
        kind, value = self.filter_kind, self.filter_value
        if kind == "status":
            return self.status_match(t, value)
        if kind == "category":
            return value == "*" or str(t.get("category", "") or "") == value
        if kind == "tag":
            tags = self._tags(t)
            return value == "*" or (value == "" and not tags) or value in tags
        if kind == "tracker":
            return value == "*" or self._tracker_name(t) == value
        return True

    def _freeze_sort_values(self, column: int) -> None:
        self._frozen_sort_column = column
        self._frozen_sort = {}
        if column < 0 or column >= len(COLUMNS):
            return
        key = COLUMNS[column][0]
        for row, h in enumerate(self.source.order):
            t = self.source.torrents.get(h, {})
            self._frozen_sort[h] = self.source._raw_value(t, key)

    def set_live_sorting(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.setDynamicSortFilter(enabled)
        if enabled:
            self._frozen_sort.clear()
            self._frozen_sort_column = -1
        else:
            col = self.sortColumn()
            if col >= 0:
                self._freeze_sort_values(col)

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder):
        # qBittorrent keeps live data flowing without making the view unusable.
        # With Live Sorting disabled, capture the values at the moment the user
        # explicitly sorts. Filter refreshes can then rebuild the proxy mapping
        # without letting changing speeds/progress reshuffle existing rows.
        if column >= 0 and not self.dynamicSortFilter():
            self._freeze_sort_values(column)
        elif self.dynamicSortFilter():
            self._frozen_sort.clear()
            self._frozen_sort_column = -1
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if (not self.dynamicSortFilter()) and left.column() == self._frozen_sort_column:
            lh = str(left.data(HASH_ROLE) or "")
            rh = str(right.data(HASH_ROLE) or "")
            lv = self._frozen_sort.get(lh, left.data(RAW_ROLE))
            rv = self._frozen_sort.get(rh, right.data(RAW_ROLE))
        else:
            lv = left.data(RAW_ROLE)
            rv = right.data(RAW_ROLE)
        try:
            return lv < rv
        except Exception:
            return str(lv).casefold() < str(rv).casefold()

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from rqbt.common import APP_NAME, RELEASE_ID, DARK_STYLE, LOG_DIR, LOG_FILE, make_app_icon
from rqbt.config import load_config
from rqbt.mainwindow import MainWindow
from rqbt.windows_integration import register_associations, unregister_associations
from rqbt.updater import UPDATE_LOG_FILE, consume_update_result

INSTANCE_NAME = "RemoteQBT.SingleInstance"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def shell_sources(argv: list[str]) -> list[str]:
    out: list[str] = []
    for arg in argv:
        a = arg.strip().strip('"')
        if not a:
            continue
        if a.lower().startswith(("magnet:?", "http://", "https://")):
            out.append(a)
        elif Path(a).is_file() and Path(a).suffix.lower() == ".torrent":
            out.append(str(Path(a).resolve()))
    return out


def send_to_existing(sources: list[str]) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_NAME)
    if not sock.waitForConnected(250):
        return False
    payload = json.dumps({"sources": sources}).encode("utf-8")
    sock.write(QByteArray(payload))
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def main() -> int:
    setup_logging()

    # Headless maintenance switches used by the installer/uninstaller.
    if "--register-associations" in sys.argv:
        ok, msg = register_associations()
        print(msg)
        return 0 if ok else 1
    if "--unregister-associations" in sys.argv:
        ok, msg = unregister_associations()
        print(msg)
        return 0 if ok else 1

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(RELEASE_ID)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLE)
    app.setWindowIcon(make_app_icon())

    sources = shell_sources(sys.argv[1:])
    if send_to_existing(sources):
        return 0

    # Own the single-instance endpoint. Remove stale endpoints left after crashes.
    QLocalServer.removeServer(INSTANCE_NAME)
    server = QLocalServer()
    if not server.listen(INSTANCE_NAME):
        QMessageBox.critical(None, APP_NAME, f"Could not create the RemoteQBT single-instance endpoint:\n\n{server.errorString()}")
        return 2

    win = MainWindow(sources)
    win.show()

    def show_update_result():
        result = consume_update_result()
        if not result:
            return
        status = str(result.get("status", "")).lower()
        release_id = str(result.get("release_id", "") or RELEASE_ID)
        message = str(result.get("message", "")).strip()
        if status == "success":
            QMessageBox.information(
                win,
                "RemoteQBT Update",
                f"RemoteQBT {release_id} was installed successfully.\n\n"
                "You are already on the updated build.",
            )
        elif status == "failed":
            QMessageBox.warning(
                win,
                "RemoteQBT Update Failed",
                "The update did not complete and RemoteQBT restored the previous installation.\n\n"
                + (message + "\n\n" if message else "")
                + f"Update log:\n{UPDATE_LOG_FILE}",
            )

    QTimer.singleShot(450, show_update_result)

    def receive_connection():
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()

            def read_socket(s=sock):
                if s.bytesAvailable() <= 0:
                    if not s.waitForReadyRead(300):
                        s.deleteLater()
                        return
                try:
                    payload = bytes(s.readAll()).decode("utf-8", errors="replace")
                    data = json.loads(payload or "{}")
                    incoming = [str(x) for x in data.get("sources", []) if str(x)]
                    if incoming:
                        win.accept_external_sources(incoming)
                    win.showNormal()
                    win.raise_()
                    win.activateWindow()
                except Exception:
                    logging.getLogger(APP_NAME).exception("Failed to receive shell source")
                finally:
                    s.disconnectFromServer()
                    s.deleteLater()

            if sock.bytesAvailable() > 0:
                read_socket()
            else:
                sock.readyRead.connect(read_socket)
                QTimer.singleShot(350, read_socket)

    server.newConnection.connect(receive_connection)

    # Best-effort integration repair after a normal launch. This does not override
    # Windows UserChoice policy; it simply keeps RemoteQBT registered as a handler.
    try:
        cfg = load_config()
        if bool(cfg.get("integrate_windows", True)) and os.name == "nt":
            register_associations()
    except Exception:
        logging.getLogger(APP_NAME).exception("Windows association registration failed")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

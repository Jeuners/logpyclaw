"""
AgentClaw — macOS App Entry Point
Startet Flask intern und öffnet ein natives macOS WebView-Fenster.
"""

import threading
import socket
import time
import sys
import os
import signal

PID_FILE = os.path.expanduser("~/.agentclaw.pid")


def cleanup_old_process():
    """Prüft und beendet ggf. alte Processe."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            # Prüfen ob Prozess noch läuft
            try:
                os.kill(old_pid, 0)
                # Prozess läuft noch - beenden
                print(f"[AgentClaw] Beende alter Prozess {old_pid}...")
                os.kill(old_pid, signal.SIGTERM)
                time.sleep(1)
            except OSError:
                pass  # Prozess läuft nicht mehr
        except:
            pass

    # Eigene PID schreiben
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


# Sicherstellen dass das Verzeichnis korrekt ist (wichtig für py2app)
# Contents/MacOS/AgentClaw → Contents/Resources/ (wo templates, static, agents.json liegen)
if getattr(sys, "frozen", False):
    _resources = os.path.join(
        os.path.dirname(os.path.dirname(sys.executable)), "Resources"
    )
    os.chdir(_resources)

import webview


def find_free_port(start=5050, end=5099):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    return start


def start_flask(port):
    from app import app, socketio

    socketio.run(
        app,
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


def wait_for_server(port, timeout=10):
    """Warte bis Flask bereit ist."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    cleanup_old_process()  # Beende alte Instanzen vor dem Start
    port = find_free_port()

    # Flask in Background-Thread starten
    flask_thread = threading.Thread(
        target=start_flask,
        args=(port,),
        daemon=True,
    )
    flask_thread.start()

    # Warten bis Server antwortet
    if not wait_for_server(port):
        webview.create_window(
            "AgentClaw — Fehler",
            html="<h2 style='font-family:sans-serif;color:red'>Flask konnte nicht gestartet werden.</h2>",
        )
        webview.start()
        return

    # Natives macOS Fenster öffnen
    window = webview.create_window(
        title="AgentClaw",
        url=f"http://127.0.0.1:{port}",
        width=1440,
        height=920,
        min_size=(900, 600),
        text_select=True,
        zoomable=True,
    )

    webview.start(
        debug=False,
        private_mode=False,
    )


if __name__ == "__main__":
    main()

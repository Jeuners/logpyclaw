"""
core/flask_app.py — Flask + SocketIO Instanz.
Kein Import aus anderen eigenen Modulen (Ebene 0).
"""
import os
import sys
from flask import Flask
from flask_socketio import SocketIO

# ── py2app Bundle Support ─────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    # Contents/MacOS/AgentClaw → Contents/Resources/
    _bundle_resources = os.path.join(
        os.path.dirname(os.path.dirname(sys.executable)), "Resources"
    )
    app = Flask(
        __name__,
        template_folder=os.path.join(_bundle_resources, "templates"),
        static_folder=os.path.join(_bundle_resources, "static"),
    )
    os.chdir(_bundle_resources)
else:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=30,
    ping_interval=10,
)

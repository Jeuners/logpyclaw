"""
AgentClaw — py2app Build Script
Build:  python setup.py py2app
Output: dist/AgentClaw.app
"""

from setuptools import setup
import os

APP = ["main_app.py"]
APP_NAME = "AgentClaw"

# Alle Daten-Dateien die ins Bundle müssen
DATA_FILES = [
    ("templates", ["templates/index.html"]),
    ("", ["agents.json", "providers.json", "tasks.json", "app.py"]),
    (
        "avatars",
        [
            "avatars/Picasso.jpg",
            "avatars/LISA.jpg",
            "avatars/Flo.png",
            "avatars/MARTIN.png",
            "avatars/Jan.jpg",
            "avatars/Fotograf.jpg",
        ],
    ),
]

# Statische Dateien (css / js Unterordner) dynamisch einsammeln
for dirpath, dirnames, filenames in os.walk("static"):
    files = [os.path.join(dirpath, f) for f in filenames]
    if files:
        DATA_FILES.append((dirpath, files))

OPTIONS = {
    "argv_emulation": False,  # WICHTIG: False für pywebview
    "semi_standalone": True,
    "site_packages": True,
    # Explizit einzubindende Pakete
    "packages": [
        "flask",
        "jinja2",
        "werkzeug",
        "click",
        "itsdangerous",
        "markupsafe",
        "webview",
        "requests",
        "urllib3",
        "certifi",
        "idna",
        "charset_normalizer",
        "dotenv",
        "qdrant_client",
        "pydantic",
        "pydantic_core",
        "grpc",
        "httpx",
        "httpcore",
        "anyio",
        "h2",
        "hpack",
        "hyperframe",
        "portalocker",
        "proxy_tools",
        "bottle",
        "objc",
        "redis",
    ],
    # Explizit zu importierende Module (werden sonst manchmal übersehen)
    "includes": [
        "app",  # AgentClaw Flask-App (dynamisch in start_flask() importiert)
        "objc",
        "Foundation",
        "AppKit",
        "WebKit",
        "Quartz",
        "threading",
        "socket",
        "time",
        "json",
        "uuid",
        "os",
        "sys",
        "io",
        "base64",
        "re",
        "subprocess",
        "pathlib",
    ],
    "excludes": [
        "tkinter",
        "test",
        "distutils",
        "unittest",
        "email.test",
        "xmlrpc",
        "lib2to3",
        "doctest",
    ],
    # App-Metadaten
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.agentclaw.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,  # Dark Mode support
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "2025 AgentClaw",
        # Berechtigungen
        "NSMicrophoneUsageDescription": "AgentClaw nutzt das Mikrofon für Spracheingabe.",
        "NSCameraUsageDescription": "AgentClaw benötigt ggf. Kamerazugriff.",
        # Damit Webview lokale Ressourcen laden kann
        "NSAppTransportSecurity": {
            "NSAllowsArbitraryLoads": True,
            "NSAllowsLocalNetworking": True,
        },
        # Activity Bar / Full-Screen support
        "NSSupportsAutomaticGraphicsSwitching": True,
        "LSUIElement": False,  # Zeige im Dock
    },
    # App-Icon
    "iconfile": "AgentClaw.icns",
}

setup(
    app=APP,
    name=APP_NAME,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

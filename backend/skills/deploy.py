"""
backend/skills/deploy.py — Deploy-Skill für dev.dillenberg.net.

Syntax:
  deploy <slug>                    → frontend/builds/<slug>/ → dev.dillenberg.net/<slug>/
  deploy <file.html> as <slug>     → single-file Deploy
  deploy <path> as <slug>          → arbitrary path → dev/<slug>/
  list deploys                     → zeigt alle deploys
  undeploy <slug>                  → entfernt
  show deploys / list              → kurzer Index

Architektur:
  rsync via SSH zum c2 (Mac hat schon Key-Auth)
  Auto-Update von /__deploys.json (Index)
  Auto-Regenerate von /index.html (HTML Index)
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path

from backend.skills import Skill, SkillConfigField

REPO_ROOT      = Path(__file__).resolve().parent.parent.parent
BUILDS_DIR     = REPO_ROOT / "frontend" / "builds"
FRONTEND_DIR   = REPO_ROOT / "frontend"
META_FILE      = BUILDS_DIR / ".deploys.json"

_SSH_HOST    = "root@c2.webbinder.de"
_REMOTE_ROOT = "/var/www/dev.dillenberg.net"
_PUBLIC_BASE = "https://dev.dillenberg.net"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load_meta() -> dict:
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            pass
    return {"deploys": {}}


def _save_meta(meta: dict) -> None:
    META_FILE.write_text(json.dumps(meta, indent=2))


def _validate_slug(slug: str) -> str | None:
    s = slug.strip().lower()
    return s if _SLUG_RE.match(s) else None


class DeploySkill(Skill):
    skill_id    = "deploy"
    description = (
        "Deployed Builds auf dev.dillenberg.net via rsync/SSH. "
        "Syntax: 'deploy <slug>', 'deploy <file> as <slug>', 'list deploys', 'undeploy <slug>'."
    )
    CONFIG_FIELDS = (
        SkillConfigField("ssh_host",    env="DEPLOY_SSH_HOST",    default=_SSH_HOST),
        SkillConfigField("remote_root", env="DEPLOY_REMOTE_ROOT", default=_REMOTE_ROOT),
        SkillConfigField("public_base", env="DEPLOY_PUBLIC_BASE", default=_PUBLIC_BASE),
    )

    async def execute(self, query: str) -> str:
        q = query.strip()
        ql = q.lower()

        # List
        if re.match(r"(list|show|alle)\s+(deploys?|builds?|deployments?)", ql) or ql in ("list", "deploys"):
            return self._format_list()

        # Undeploy
        m = re.match(r"(?:undeploy|delete|remove)\s+(?:deploy\s+)?(\S+)", q, re.I)
        if m:
            return await self._undeploy(m.group(1))

        # Deploy <file> as <slug>
        m = re.match(r"deploy\s+(\S+)\s+(?:as|→|to)\s+(\S+)", q, re.I)
        if m:
            return await self._deploy(m.group(1), m.group(2))

        # Deploy <slug>  (default: frontend/builds/<slug>/)
        m = re.match(r"(?:deploy|publish|publiziere)\s+(\S+)", q, re.I)
        if m:
            arg = m.group(1)
            # Wenn arg ein Pfad ist, Slug aus Datei-Namen ableiten
            if "/" in arg or "." in arg:
                slug = Path(arg).stem.lower().replace(" ", "-")
                slug = re.sub(r"[^a-z0-9_-]", "", slug)
                return await self._deploy(arg, slug)
            return await self._deploy(None, arg)

        return self._usage()

    # ── Deploy ────────────────────────────────────────────────────────────────

    async def _deploy(self, source: str | None, slug: str) -> str:
        slug = _validate_slug(slug) or ""
        if not slug:
            return f"[Deploy] Ungültiger Slug. Erlaubt: a-z 0-9 _ - (max 40 chars)"

        # Quelle bestimmen
        if source:
            src_path = Path(source)
            if not src_path.is_absolute():
                src_path = REPO_ROOT / source
            if not src_path.exists():
                # Vielleicht als frontend/<source>
                alt = FRONTEND_DIR / source
                if alt.exists():
                    src_path = alt
                else:
                    return f"[Deploy] Quelle nicht gefunden: {source}"
        else:
            src_path = BUILDS_DIR / slug
            if not src_path.exists():
                return (
                    f"[Deploy] Build-Verzeichnis fehlt: frontend/builds/{slug}/\n"
                    f"Lege es an oder nutze 'deploy <pfad> as {slug}'"
                )

        cfg = self.config
        remote_root = cfg.get("remote_root", _REMOTE_ROOT)
        ssh_host    = cfg.get("ssh_host",    _SSH_HOST)
        public_base = cfg.get("public_base", _PUBLIC_BASE)
        remote_path = f"{remote_root}/{slug}/"

        # Wenn Single-File: in tmp-dir verpacken
        cleanup_tmp: Path | None = None
        if src_path.is_file():
            import tempfile
            tmp = Path(tempfile.mkdtemp(prefix="deploy-"))
            target = tmp / "index.html"
            shutil.copy2(src_path, target)
            src_path = tmp
            cleanup_tmp = tmp

        # rsync
        rsync_src = str(src_path).rstrip("/") + "/"
        cmd = [
            "rsync", "-rltz", "--delete",
            rsync_src, f"{ssh_host}:{remote_path}",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                return f"[Deploy] rsync-Fehler ({proc.returncode}):\n{err.decode()[:400]}"

            # Permissions: dirs 755, files 644 (X = nur für dirs/executables)
            proc2 = await asyncio.create_subprocess_exec(
                "ssh", ssh_host, f"chmod -R u=rwX,go=rX {remote_path}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc2.communicate(), timeout=10)
        except TimeoutError:
            return "[Deploy] rsync timeout (>120s)"
        finally:
            if cleanup_tmp:
                shutil.rmtree(cleanup_tmp, ignore_errors=True)

        # Größe berechnen
        size_kb = await self._remote_size(ssh_host, remote_path)

        # Meta-Update
        meta = _load_meta()
        meta["deploys"][slug] = {
            "slug":       slug,
            "deployed_at": _now(),
            "size_kb":    size_kb,
            "source":     str(source or f"builds/{slug}"),
            "url":        f"{public_base}/{slug}/",
        }
        _save_meta(meta)

        # Index regenerieren + uploaden
        await self._regenerate_index(meta, ssh_host, remote_root, public_base)

        return (
            f"[Deploy] ✅ Live: {public_base}/{slug}/\n"
            f"Größe: {size_kb} KB · Quelle: {source or 'builds/'+slug}"
        )

    async def _undeploy(self, slug: str) -> str:
        slug = _validate_slug(slug) or ""
        if not slug:
            return "[Deploy] Ungültiger Slug"

        cfg = self.config
        ssh_host    = cfg.get("ssh_host",    _SSH_HOST)
        remote_root = cfg.get("remote_root", _REMOTE_ROOT)
        public_base = cfg.get("public_base", _PUBLIC_BASE)
        remote_path = f"{remote_root}/{slug}"

        cmd = ["ssh", ssh_host, f"rm -rf {remote_path}"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return f"[Deploy] Entfernen fehlgeschlagen (exit {proc.returncode})"

        meta = _load_meta()
        meta["deploys"].pop(slug, None)
        _save_meta(meta)
        await self._regenerate_index(meta, ssh_host, remote_root, public_base)

        return f"[Deploy] 🗑️ Entfernt: {slug}"

    # ── List / Format ─────────────────────────────────────────────────────────

    def _format_list(self) -> str:
        meta = _load_meta()
        deploys = list(meta.get("deploys", {}).values())
        if not deploys:
            return "[Deploy] Keine Deploys vorhanden."
        deploys.sort(key=lambda d: d.get("deployed_at", ""), reverse=True)
        lines = [f"[Deploy] {len(deploys)} aktive Deploys:\n"]
        for d in deploys:
            lines.append(f"  ▸ {d['slug']:<20} {d['deployed_at']}  {d['size_kb']}KB  {d['url']}")
        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _remote_size(self, ssh_host: str, remote_path: str) -> int:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", ssh_host, f"du -sk {remote_path} | cut -f1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return int(out.decode().strip() or 0)
        except Exception:
            return 0

    async def _regenerate_index(
        self, meta: dict, ssh_host: str, remote_root: str, public_base: str
    ) -> None:
        deploys = list(meta.get("deploys", {}).values())
        deploys.sort(key=lambda d: d.get("deployed_at", ""), reverse=True)

        # JSON-Index für Programme
        json_payload = json.dumps(
            {"deploys": deploys, "generated_at": _now()},
            indent=2,
        )

        # HTML-Index für Menschen
        rows = ""
        for d in deploys:
            rows += f"""
        <li class="deploy">
          <a href="/{d['slug']}/" class="slug">{d['slug']}</a>
          <span class="ts">{d['deployed_at']}</span>
          <span class="size">{d['size_kb']} KB</span>
          <a href="/{d['slug']}/" class="open">open ↗</a>
        </li>"""
        empty_msg = '<div class="empty">// no deploys yet</div>' if not deploys else ''
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>dev.dillenberg.net · {len(deploys)} deploys</title>
<meta name="robots" content="noindex,nofollow">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#0a0a0b;--surf:#15151a;--line:rgba(255,255,255,.08);--text:#f5f6f7;--text2:#a9adb4;--text3:#6e7480;--brand:#ff5e1f;--signal:#00ffae}}
body{{background:var(--bg);color:var(--text);font:14px/1.55 ui-monospace,'JetBrains Mono',monospace;padding:48px 24px;max-width:980px;margin:0 auto;-webkit-font-smoothing:antialiased}}
header{{display:flex;align-items:center;gap:14px;margin-bottom:8px}}
header .glyph{{width:26px;height:26px;background:linear-gradient(135deg,var(--brand),#ff7a3d);border-radius:6px;display:grid;place-items:center;color:#000;font-weight:900;font-size:13px}}
h1{{font:700 22px/1 ui-monospace;letter-spacing:0.04em}}
h1 .sep{{color:var(--brand)}}
.meta{{color:var(--text3);font-size:11px;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:40px}}
.stats{{display:flex;gap:32px;padding:18px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin-bottom:24px;flex-wrap:wrap}}
.stat .v{{color:var(--text);font-size:18px}}
.stat .k{{color:var(--text3);font-size:10px;letter-spacing:0.16em;text-transform:uppercase;margin-top:2px}}
ul{{list-style:none;display:flex;flex-direction:column;gap:6px}}
.deploy{{display:grid;grid-template-columns:1fr 200px 80px 80px;gap:18px;align-items:center;padding:12px 16px;background:var(--surf);border:1px solid var(--line);border-radius:8px;transition:border-color .15s,background .15s}}
.deploy:hover{{border-color:rgba(255,94,31,.4);background:#1c1c22}}
.slug{{color:var(--text);font-weight:600;text-decoration:none;font-size:14px}}
.slug:hover{{color:var(--brand)}}
.ts{{color:var(--text3);font-size:11px}}
.size{{color:var(--text3);font-size:11px;text-align:right}}
.open{{color:var(--signal);text-decoration:none;font-size:11px;text-align:right}}
.open:hover{{color:#6effa3}}
.empty{{opacity:.4;padding:40px 0;text-align:center}}
footer{{margin-top:48px;padding-top:24px;border-top:1px solid var(--line);color:var(--text3);font-size:10px;letter-spacing:0.04em;display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px}}
footer a{{color:var(--brand);text-decoration:none}}
@media(max-width:680px){{.deploy{{grid-template-columns:1fr;gap:6px}}.ts,.size,.open{{text-align:left}}}}
</style>
</head>
<body>
<header><span class="glyph">L</span><h1>dev<span class="sep">·</span>dillenberg<span class="sep">·</span>net</h1></header>
<div class="meta">agent-deployed staging environment</div>

<div class="stats">
  <div class="stat"><div class="v">{len(deploys)}</div><div class="k">active deploys</div></div>
  <div class="stat"><div class="v">{sum(d.get('size_kb',0) for d in deploys)} KB</div><div class="k">total size</div></div>
  <div class="stat"><div class="v">{deploys[0]['deployed_at'] if deploys else '—'}</div><div class="k">last deploy</div></div>
</div>

<ul>
{rows or ''}
</ul>
{empty_msg}

<footer>
  <div>powered by <a href="#">LogpyClaw</a> · pushed via <code>skill:deploy</code></div>
  <div>regenerated {_now()}</div>
</footer>
</body>
</html>
"""

        # Beides aufs c2 schreiben (per ssh in tmp + mv)
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(html)
            html_path = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json_payload)
            json_path = f.name

        try:
            for src, target in [(html_path, "index.html"), (json_path, "__deploys.json")]:
                proc = await asyncio.create_subprocess_exec(
                    "rsync", "-tz", src, f"{ssh_host}:{remote_root}/{target}",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
            # chmod 644 für die Index-Files
            proc_chmod = await asyncio.create_subprocess_exec(
                "ssh", ssh_host,
                f"chmod 644 {remote_root}/index.html {remote_root}/__deploys.json",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc_chmod.communicate(), timeout=10)
        finally:
            Path(html_path).unlink(missing_ok=True)
            Path(json_path).unlink(missing_ok=True)

    def _usage(self) -> str:
        return (
            "[Deploy] Befehle:\n"
            "  deploy <slug>            — frontend/builds/<slug>/ → dev/<slug>/\n"
            "  deploy <file> as <slug>  — single-file deploy\n"
            "  deploy <path> as <slug>  — beliebiger Pfad\n"
            "  list deploys             — alle aktiven Deploys\n"
            "  undeploy <slug>          — vom Server entfernen"
        )

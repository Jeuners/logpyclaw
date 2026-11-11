#!/usr/bin/env python3
"""
scripts/load_claude_memory.py — Claude.ai-Memory-Export in Martins Gedächtnis laden.

Parst memories.json (conversations_memory + project_memories) aus einem
Claude.ai-Datenexport und legt es im Scope agent:martin ab — abschnittsweise,
damit Martin gezielt die relevante Stelle erinnert.

  python scripts/load_claude_memory.py /pfad/zu/claudememory   [--scope agent:martin]
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.core.ingest import ingest_text  # noqa: E402
from backend.core.memory import SemanticMemory  # noqa: E402


def find_memories_json(root: str) -> str | None:
    if os.path.isfile(root) and root.endswith(".json"):
        return root
    hits = glob.glob(os.path.join(root, "**", "memories.json"), recursive=True)
    return hits[0] if hits else None


async def main():
    args = sys.argv[1:]
    scope = "agent:martin"
    if "--scope" in args:
        i = args.index("--scope")
        scope = args[i + 1]
        del args[i:i + 2]
    root = args[0] if args else os.path.expanduser("~/Downloads/AgentClaw/claudememory")

    path = find_memories_json(root)
    if not path:
        print(f"✗ keine memories.json gefunden unter {root}")
        return
    print(f"→ lese {path}")
    data = json.load(open(path, encoding="utf-8"))
    rec = data[0] if isinstance(data, list) else data

    mem = SemanticMemory()
    total = 0

    # 1. conversations_memory → je **Abschnitt** eine Erinnerung
    cm = rec.get("conversations_memory", "")
    if cm:
        # an **Überschrift**-Markern splitten
        parts = re.split(r"\n(?=\*\*[^*]+\*\*)", cm.strip())
        for part in parts:
            hm = re.match(r"\*\*(.+?)\*\*", part)
            title = hm.group(1) if hm else "Kontext"
            n = await ingest_text(
                mem, part, scope, kind="claude_memory",
                source="claude.ai", base_meta={"section": title},
            )
            total += n
            print(f"  ✓ [{title}] → {n} Häppchen")

    # 2. project_memories → je Projekt eine (oder mehrere) Erinnerung(en)
    pm = rec.get("project_memories", {})
    if isinstance(pm, dict):
        for pid, ptext in pm.items():
            if not (ptext or "").strip():
                continue
            n = await ingest_text(
                mem, ptext, scope, kind="claude_project",
                source="claude.ai/project", base_meta={"project_id": pid},
            )
            total += n
        print(f"  ✓ {len(pm)} Projekt-Memories → eingebettet")

    print(f"\n✅ Fertig: {total} Häppchen in Scope '{scope}' abgelegt.")
    print("   stats:", mem.stats())


if __name__ == "__main__":
    asyncio.run(main())

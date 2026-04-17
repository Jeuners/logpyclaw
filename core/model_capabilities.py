"""
core/model_capabilities.py — Erkennt Fähigkeiten von LLM-Modellen.

Hauptzweck: Feststellen ob ein Modell "thinking" (Chain-of-Thought)
unterstützt. Wird von ChatService/llm_stream genutzt, um Ollama
`"think": true` nur bei reasoning-fähigen Modellen anzufragen.
"""
import logging
import re
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

# Name-Pattern für bekannte Reasoning-Modelle (alle lowercase matched)
REASONING_MODEL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bqwen3"),          # qwen3, qwen3.5, qwen3-xx
    re.compile(r"qwq"),              # qwen/QwQ
    re.compile(r"deepseek-?r1"),     # deepseek-r1, deepseek_r1
    re.compile(r"\bgpt-oss"),        # gpt-oss (OpenAI reasoning)
    re.compile(r"magistral"),        # Mistral Magistral
    re.compile(r"\bo1\b|\bo3\b|\bo4\b"),  # OpenAI o1/o3/o4
    re.compile(r"reason|thinking"),  # generisch: "*-thinking", "*-reasoner"
]


def _matches_pattern(model: str) -> bool:
    m = model.lower()
    return any(rx.search(m) for rx in REASONING_MODEL_PATTERNS)


@lru_cache(maxsize=64)
def _probe_ollama_capabilities(model: str, ollama_url: str) -> frozenset:
    """Fragt Ollama /api/show nach den Fähigkeiten eines Modells.

    Ollama liefert seit v0.4 ein `capabilities`-Feld (z.B. ["completion",
    "tools", "thinking"]). Gecached, damit wir nicht bei jedem Call anfragen.
    """
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(f"{ollama_url.rstrip('/')}/api/show",
                            json={"name": model})
            if r.status_code == 200:
                data = r.json()
                caps = data.get("capabilities") or []
                return frozenset(c.lower() for c in caps)
    except Exception as e:
        logger.debug("Ollama capability-probe für %s fehlgeschlagen: %s", model, e)
    return frozenset()


def supports_thinking(model: str, provider: str = "ollama",
                      ollama_url: str = "http://localhost:11434") -> bool:
    """Prüft ob ein Modell Chain-of-Thought / Thinking unterstützt.

    Strategie:
    1. Namens-Pattern (schnell, offline) — deckt bekannte Reasoning-Modelle.
    2. Für Ollama zusätzlich: /api/show.capabilities (autoritativ).

    Returns:
        True wenn das Modell wahrscheinlich reasoning kann, sonst False.
    """
    if not model:
        return False

    # Pattern-Match zuerst (billig, immer verfügbar)
    if _matches_pattern(model):
        return True

    # Ollama-Probe als autoritativer Fallback
    if provider == "ollama":
        caps = _probe_ollama_capabilities(model, ollama_url)
        if "thinking" in caps or "reasoning" in caps:
            return True

    return False


# Regex um <think>...</think> Blöcke aus älteren Reasoning-Modellen zu
# extrahieren (qwen3 pre-thinking-field, deepseek-r1 Stil).
THINK_TAG_RX = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def split_thinking_and_content(text: str) -> tuple[str, str]:
    """Trennt <think>-Blöcke vom eigentlichen Antwort-Content.

    Returns: (thinking_text, cleaned_content)
    """
    if not text or "<think>" not in text.lower():
        return "", text
    thoughts = "\n\n".join(m.group(1).strip() for m in THINK_TAG_RX.finditer(text))
    cleaned = THINK_TAG_RX.sub("", text).strip()
    return thoughts, cleaned

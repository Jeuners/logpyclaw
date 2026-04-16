"""Prompt optimization skill using Ollama + framework templates."""
import json
import os

import requests

from .triggers import PROMPT_FRAMEWORKS


def _load_providers() -> dict:
    try:
        from core.config import PROVIDERS_FILE
        path = PROVIDERS_FILE
    except Exception:
        path = os.path.join(os.getcwd(), "providers.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def optimize_prompt(
    input_prompt: str, framework_id: str = "RTF", target_model: str = "General LLM"
) -> str:
    """Optimize a prompt using the specified framework via Ollama."""
    fw = PROMPT_FRAMEWORKS.get(framework_id.upper(), PROMPT_FRAMEWORKS["RTF"])
    providers = _load_providers()
    ollama_url = (
        providers.get("ollama", {}).get("url", "http://localhost:11434").rstrip("/")
    )

    system_prompt = "You are an elite Prompt Engineering Expert. Respond ONLY with valid JSON, no markdown."
    user_prompt = f"""Optimize this prompt using the {framework_id} framework ({"-".join(fw["steps"])}).

TARGET MODEL: {target_model}
BEST FOR: {fw["best_for"]}
USER DRAFT: "{input_prompt}"

Deconstruct, refine each step, explain why each change helps, then build the final prompt.

Respond with this exact JSON:
{{
  "refinedPrompt": "the final optimized prompt",
  "breakdown": [
    {{"step": "step name", "content": "content for this step", "explanation": "why this works"}}
  ],
  "generalAdvice": "overall advice in 1-2 sentences"
}}"""

    # Try to find a capable model
    ollama_model = "gemma3:latest"
    try:
        models_resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if models_resp.ok:
            names = [m["name"] for m in models_resp.json().get("models", [])]
            for preferred in [
                "gemma3:latest",
                "mistral-nemo:12b",
                "llama3.1:8b",
                "gemma3:12b",
            ]:
                if preferred in names:
                    ollama_model = preferred
                    break
    except Exception:
        pass

    resp = requests.post(
        f"{ollama_url}/api/generate",
        json={
            "model": ollama_model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "format": "json",
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        result = json.loads(data["response"])
    except json.JSONDecodeError as e:
        print(f"[prompt/optimize] JSON parse error: {e}", flush=True)
        print(f"[prompt/optimize] Raw response: {data['response'][:500]}", flush=True)
        return input_prompt  # Fallback to original

    refined = result.get("refinedPrompt", "")
    breakdown = result.get("breakdown", [])
    advice = result.get("generalAdvice", "")

    lines = [
        f"✨ **Optimized Prompt** ({framework_id} — {fw['name']})",
        "",
        f"```",
        refined,
        f"```",
        "",
        f"**Breakdown:**",
    ]
    for step in breakdown:
        lines.append(f"- **{step.get('step', '')}**: {step.get('content', '')}  ")
        lines.append(f"  _{step.get('explanation', '')}_")
    if advice:
        lines += ["", f"💡 {advice}"]
    return "\n".join(lines)


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult
from skills.triggers import PROMPT_OPTIMIZE_TRIGGERS


class PromptOptimizeSkill(BaseSkill):
    id = "prompt_optimize"
    name = "Prompt Optimizer"
    icon = "auto_fix_high"
    description = "Optimizes prompts using structured frameworks (RTF, TAG, BAB, CARE, RISE)."
    triggers = [PROMPT_OPTIMIZE_TRIGGERS.pattern]
    requires = []

    def matches(self, message: str) -> bool:
        return bool(PROMPT_OPTIMIZE_TRIGGERS.search(message))

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        try:
            result = optimize_prompt(message)
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)

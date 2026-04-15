#!/usr/bin/env python3
"""
llm.py — Local LLM caller via Ollama.

Usage (as a library):
    from llm import call_local

call_local(prompt, system, timeout, temperature)  → str

Reads connection settings from config (loaded from .env):
    LOCAL_LLM_URL=http://localhost:11434/api/generate
    LOCAL_LLM_MODEL=gemma4:26b

Temperature:
    Default is 0.2 — low for factual, consistent wiki content.
    Pass a higher value (e.g. 0.7) only for creative/generative tasks.

All LLM work in brain-wiki runs locally. No API keys required.
Queries are synthesized by Claude Code directly from printed wiki context.
"""

import json
import urllib.error
import urllib.request

_DEFAULT_URL = "http://localhost:11434/api/generate"
_DEFAULT_MODEL = "gemma4:26b"
_DEFAULT_TEMPERATURE = 0.2


def call_local(
    prompt: str,
    system: str,
    timeout: int = 300,
    temperature: float = _DEFAULT_TEMPERATURE,
) -> str:
    """Call the local LLM via Ollama."""
    from config import cfg

    url = cfg.llm_url
    model = cfg.llm_model

    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))["response"].strip()
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach local LLM at {url}\n"
            f"Check LOCAL_LLM_URL in your .env\n"
            f"Make sure Ollama is running:  ollama serve\n"
            f"And the model is pulled:      ollama pull {model}\n"
            f"Details: {e}"
        )

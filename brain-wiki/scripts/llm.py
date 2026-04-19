#!/usr/bin/env python3
"""
llm.py — Local LLM caller via Ollama with streaming and timed logging.

Usage (as a library):
    from llm import call_local

call_local(prompt, system, timeout, temperature, label)  -> str

    label    Short description shown in the timed log (e.g. "classify", "wiki page")
             If omitted, no step label is printed.

Streaming:
    Tokens are printed to stderr as they arrive so you can watch generation in real time.
    The complete response string is returned when done.

Timed logging:
    Prints elapsed time for each call:
        [classify] ... done in 4.2s
        [wiki page] ... done in 127.8s

Temperature:
    Default 0.2 — factual and consistent. Pass higher for creative tasks.
"""

import json
import sys
import time
import urllib.error
import urllib.request

_DEFAULT_TEMPERATURE = 0.2


def call_local(
    prompt: str,
    system: str,
    timeout: int = 300,
    temperature: float = _DEFAULT_TEMPERATURE,
    label: str = "",
) -> str:
    """Call the local LLM via Ollama with streaming output and elapsed timing."""
    from config import cfg

    url = cfg.llm_url
    model = cfg.llm_model

    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,  # stream tokens as they arrive
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

    step = f"[{label}]" if label else "[llm]"
    print(f"  {step} starting...", file=sys.stderr)
    t_start = time.time()

    try:
        full_response = []
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Print a header line then stream tokens inline
            sys.stderr.write(f"  {step} ")
            sys.stderr.flush()

            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                token = chunk.get("response", "")
                if token:
                    full_response.append(token)
                    sys.stderr.write(token)
                    sys.stderr.flush()

                if chunk.get("done", False):
                    break

        elapsed = time.time() - t_start
        sys.stderr.write(f"\n  {step} done in {elapsed:.1f}s\n")
        sys.stderr.flush()

        return "".join(full_response).strip()

    except urllib.error.URLError as e:
        elapsed = time.time() - t_start
        sys.stderr.write(f"\n  {step} failed after {elapsed:.1f}s\n")
        raise RuntimeError(
            f"Could not reach local LLM at {url}\n"
            f"Check LOCAL_LLM_URL in your .env\n"
            f"Make sure Ollama is running:  ollama serve\n"
            f"And the model is pulled:      ollama pull {model}\n"
            f"Details: {e}"
        )

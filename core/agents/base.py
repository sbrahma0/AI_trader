"""
base.py — shared Claude API wrapper for all agents.
"""

import json
import re
import time
import anthropic
from dotenv import load_dotenv
load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call_agent(model: str, prompt: str, max_tokens: int, agent_name: str) -> object:
    """
    Call Claude and return parsed JSON (list or dict).
    Strips markdown fences if present.
    """
    start = time.time()
    response = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.time() - start
    stop_reason = response.stop_reason
    print(f"  [{agent_name}] {elapsed:.1f}s  stop={stop_reason}  tokens_out={response.usage.output_tokens}")

    if stop_reason == "max_tokens":
        print(f"  WARNING: [{agent_name}] hit max_tokens — response may be truncated")

    raw = response.content[0].text.strip()
    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    return json.loads(raw)

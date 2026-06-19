"""
llm.py - optional LLM provider abstraction (§4).

Offline-first contract: **nothing here runs unless the user opts in.** With no
provider configured the app behaves exactly as before (extractive summaries,
heuristic flashcards). Providers are reached only at call time, so importing this
module never touches the network and the core app still starts with no LLM stack.

Providers
---------
Local  : ollama, llama.cpp / LM Studio (OpenAI-compatible local server)
Cloud  : openai, anthropic   (explicit opt-in; key from env or per-course settings)

Uniform entry point: :func:`complete(prompt, system, config)` returns text or
raises :class:`LLMError`. Callers (see ``app/ai.py``) catch that and fall back to
the dependency-free path.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from . import settings_store
from .database import Database


class LLMError(Exception):
    """Any provider/transport failure. Callers fall back to the offline path."""


# Per-provider default model. Cloud defaults to the latest capable Claude; all
# are overridable via per-course AI settings.
DEFAULT_MODELS = {
    "ollama": "llama3",
    "llamacpp": "local-model",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-opus-4-8",
}

CLOUD_PROVIDERS = {"openai", "anthropic"}
LOCAL_PROVIDERS = {"ollama", "llamacpp"}

# Where per-course AI config lives in the settings store.
_SETTINGS_KEY = "ai"

_DEFAULT_CONFIG = {
    "provider": "none",          # none | ollama | llamacpp | openai | anthropic
    "model": "",
    "temperature": 0.3,
    "max_tokens": 1024,
    "retrieval_depth": 5,        # chunks pulled for RAG chat
    "host": "",                  # local server base URL (ollama/llamacpp)
}


def _env_key(provider: str) -> str:
    return {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider, "")


def api_key_for(provider: str, config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve a cloud key: per-course config first, then environment.

    (Full OS-keyring storage is roadmap §10; env vars keep keys out of the DB
    for now.)"""
    if config and config.get("api_key"):
        return str(config["api_key"])
    env = _env_key(provider)
    return os.environ.get(env, "") if env else ""


def _scope_key(course_id: Optional[int]) -> str:
    return str(course_id) if course_id is not None else "_global"


def get_config(db: Optional[Database], course_id: Optional[int]) -> Dict[str, Any]:
    """Merge stored per-course AI settings over the defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    if db is not None:
        stored = settings_store.get(db, _SETTINGS_KEY) or {}
        if isinstance(stored, dict):
            scoped = stored.get(_scope_key(course_id))
            if isinstance(scoped, dict):                    # per-scope config
                cfg.update(scoped)
            elif any(k in stored for k in _DEFAULT_CONFIG):  # legacy flat config
                cfg.update(stored)
    if not cfg.get("model"):
        cfg["model"] = DEFAULT_MODELS.get(cfg.get("provider", "none"), "")
    return cfg


def set_config(db: Database, course_id: Optional[int], values: Dict[str, Any]) -> Dict[str, Any]:
    stored = settings_store.get(db, _SETTINGS_KEY) or {}
    if not isinstance(stored, dict):
        stored = {}
    key = _scope_key(course_id)
    cur = stored.get(key) if isinstance(stored.get(key), dict) else {}
    cur.update({k: v for k, v in values.items() if k in _DEFAULT_CONFIG or k == "api_key"})
    stored[key] = cur
    settings_store.set(db, _SETTINGS_KEY, stored)
    return get_config(db, course_id)


def is_enabled(config: Dict[str, Any]) -> bool:
    provider = config.get("provider", "none")
    if provider in ("none", "", None):
        return False
    if provider in CLOUD_PROVIDERS:
        return bool(api_key_for(provider, config))
    return True  # local providers need no key (reachability checked at call time)


def detect() -> Dict[str, Any]:
    """Report provider availability **without** network calls - based on installed
    SDKs and presence of API keys. Surfaced in ``/api/status`` so the UI can
    enable/disable AI features with a reason."""
    def _have(mod: str) -> bool:
        import importlib.util
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False

    return {
        "providers": {
            "ollama": {"kind": "local", "ready": _have("requests"),
                       "reason": "" if _have("requests") else "requests not installed"},
            "llamacpp": {"kind": "local", "ready": _have("requests"),
                         "reason": "" if _have("requests") else "requests not installed"},
            "openai": {"kind": "cloud", "ready": bool(api_key_for("openai")),
                       "reason": "" if api_key_for("openai") else "set OPENAI_API_KEY"},
            "anthropic": {"kind": "cloud", "ready": bool(api_key_for("anthropic")),
                          "reason": "" if api_key_for("anthropic") else "set ANTHROPIC_API_KEY"},
        },
        "default_models": DEFAULT_MODELS,
    }


# ---------------------------------------------------------------------------
# Provider transports (reached only at call time)
# ---------------------------------------------------------------------------


def _http_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
              timeout: int = 120) -> Dict[str, Any]:
    import requests
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # network / auth / transport
        raise LLMError(str(e)) from e


def _complete_ollama(prompt: str, system: str, cfg: Dict[str, Any]) -> str:
    host = cfg.get("host") or "http://127.0.0.1:11434"
    # num_predict caps the *output* tokens; without it Ollama applies a small
    # default and truncates long JSON (e.g. a 50-card deck), so honour max_tokens.
    options = {"temperature": cfg.get("temperature", 0.3)}
    try:
        options["num_predict"] = int(cfg.get("max_tokens") or 1024)
    except (TypeError, ValueError):
        pass
    payload = {
        "model": cfg["model"], "prompt": prompt, "system": system, "stream": False,
        "options": options,
    }
    # Constrain output to valid JSON when the caller asks (flashcards/quiz), so a
    # small model can't drift into prose or markdown that won't parse.
    if cfg.get("format") == "json":
        payload["format"] = "json"
    data = _http_json(host.rstrip("/") + "/api/generate", payload, headers={})
    return (data.get("response") or "").strip()


def _complete_openai_compatible(prompt: str, system: str, cfg: Dict[str, Any],
                               base: str, api_key: str) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    data = _http_json(base.rstrip("/") + "/chat/completions", {
        "model": cfg["model"], "messages": messages,
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 1024),
    }, headers=headers)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"unexpected response shape: {e}") from e


def _complete_anthropic(prompt: str, system: str, cfg: Dict[str, Any], api_key: str) -> str:
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "Content-Type": "application/json"}
    payload = {
        "model": cfg["model"], "max_tokens": cfg.get("max_tokens", 1024),
        "temperature": cfg.get("temperature", 0.3),
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system
    data = _http_json("https://api.anthropic.com/v1/messages", payload, headers=headers)
    try:
        return "".join(block.get("text", "") for block in data["content"]).strip()
    except (KeyError, TypeError) as e:
        raise LLMError(f"unexpected response shape: {e}") from e


def complete(prompt: str, *, system: str = "", config: Dict[str, Any]) -> str:
    """Single-shot completion through the configured provider. Raises LLMError
    if no provider is configured or the call fails."""
    provider = config.get("provider", "none")
    if not is_enabled(config):
        raise LLMError(f"no usable LLM provider configured (provider={provider!r})")
    cfg = dict(config)
    if not cfg.get("model"):
        cfg["model"] = DEFAULT_MODELS.get(provider, "")
    if provider == "ollama":
        return _complete_ollama(prompt, system, cfg)
    if provider == "llamacpp":
        base = cfg.get("host") or "http://127.0.0.1:8080/v1"
        return _complete_openai_compatible(prompt, system, cfg, base, "")
    if provider == "openai":
        base = cfg.get("host") or "https://api.openai.com/v1"
        return _complete_openai_compatible(prompt, system, cfg, base, api_key_for("openai", cfg))
    if provider == "anthropic":
        return _complete_anthropic(prompt, system, cfg, api_key_for("anthropic", cfg))
    raise LLMError(f"unknown provider {provider!r}")

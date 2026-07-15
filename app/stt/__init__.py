"""Adaptive offline STT platform (local models only).

Importing this package never loads Torch/Transformers/Moonshine. Heavy backends
are probed and loaded lazily through :mod:`app.stt.engines`.
"""
from __future__ import annotations

from .types import SCHEMA_VERSION, STTRequest, STTResult, Segment, Word, Profile
from .registry import registry_summary, list_models
from .router import route, explain_route
from .pipeline import transcribe_path, recommend_for_machine, available_engines
from .hardware import probe_hardware

__all__ = [
    "SCHEMA_VERSION",
    "STTRequest",
    "STTResult",
    "Segment",
    "Word",
    "Profile",
    "registry_summary",
    "list_models",
    "route",
    "explain_route",
    "transcribe_path",
    "recommend_for_machine",
    "available_engines",
    "probe_hardware",
]

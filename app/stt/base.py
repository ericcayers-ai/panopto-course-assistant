"""STT engine protocol and shared exceptions."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator, Optional, Protocol, runtime_checkable

from .types import EngineCapabilities, STTRequest, STTResult


class EngineUnavailable(RuntimeError):
    """Raised when an engine/package/model cannot be used on this machine."""


class EngineOOM(RuntimeError):
    """Raised when an engine runs out of memory; router may downshift."""


@runtime_checkable
class STTEngine(Protocol):
    name: str

    def probe(self) -> Dict[str, Any]:
        """Return installed/ready status without loading heavy weights."""
        ...

    def capabilities(self) -> EngineCapabilities:
        ...

    def load(self, model: str, device: str = "auto", **kwargs: Any) -> None:
        ...

    def unload(self) -> None:
        ...

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        ...

    def start_stream(self, request: STTRequest) -> "StreamSession":
        ...


class StreamSession(ABC):
    """Incremental streaming session for live/edge engines."""

    @abstractmethod
    def feed_audio(self, pcm16le: bytes, sample_rate: int = 16000) -> Iterator[Dict[str, Any]]:
        """Yield provisional/final segment dicts for a chunk of PCM."""

    @abstractmethod
    def finalize(self) -> STTResult:
        ...

    def close(self) -> None:
        return None


class BaseEngine(ABC):
    """Convenience base with default streaming unsupported."""

    name: str = "base"
    display_name: str = "Base"
    family: str = "base"

    @abstractmethod
    def probe(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        ...

    def load(self, model: str, device: str = "auto", **kwargs: Any) -> None:
        return None

    def unload(self) -> None:
        return None

    @abstractmethod
    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        ...

    def start_stream(self, request: STTRequest) -> StreamSession:
        raise EngineUnavailable(f"{self.name} does not support streaming")

    def is_ready(self) -> bool:
        try:
            p = self.probe()
            return bool(p.get("installed") and p.get("ready", True))
        except Exception:
            return False

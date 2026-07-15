"""Hardware / dependency probes for STT routing (real VRAM, not a 4 GB guess)."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Any, Dict, Optional

from .types import HardwareInfo


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _cuda_backend() -> Optional[str]:
    if _have("ctranslate2"):
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "ctranslate2"
        except Exception:
            pass
    if _have("torch"):
        try:
            import torch
            if torch.cuda.is_available():
                return "torch"
        except Exception:
            pass
    return None


def _gpu_details() -> Dict[str, Any]:
    """Best-effort GPU name / VRAM / compute capability."""
    out: Dict[str, Any] = {"gpu_name": "", "vram_mb": 0, "compute_capability": None}
    if _have("torch"):
        try:
            import torch
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                out["gpu_name"] = getattr(props, "name", "") or ""
                out["vram_mb"] = int(props.total_memory // (1024 * 1024))
                major = getattr(props, "major", None)
                minor = getattr(props, "minor", None)
                if major is not None and minor is not None:
                    out["compute_capability"] = f"{major}.{minor}"
                return out
        except Exception:
            pass
    # nvidia-smi fallback (no assumed 4 GB)
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,memory.total,compute_cap",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
                if parts:
                    out["gpu_name"] = parts[0]
                if len(parts) > 1:
                    out["vram_mb"] = int(float(parts[1]))
                if len(parts) > 2 and parts[2]:
                    out["compute_capability"] = parts[2]
                return out
        except Exception:
            pass
    # CTranslate2 can see CUDA without reporting VRAM — leave vram_mb=0 (unknown).
    return out


def _ram_mb() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().total // (1024 * 1024))
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys // (1024 * 1024))
        except Exception:
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size // (1024 * 1024))
    except Exception:
        return 0


def _free_disk_mb(path: Optional[str] = None) -> int:
    try:
        usage = shutil.disk_usage(path or os.getcwd())
        return int(usage.free // (1024 * 1024))
    except Exception:
        return 0


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@lru_cache(maxsize=1)
def probe_hardware(path: str = "") -> HardwareInfo:
    via = _cuda_backend()
    gpu = _gpu_details() if via else {"gpu_name": "", "vram_mb": 0, "compute_capability": None}
    return HardwareInfo(
        cuda=via is not None,
        cuda_via=via,
        gpu_name=str(gpu.get("gpu_name") or ""),
        vram_mb=int(gpu.get("vram_mb") or 0),
        compute_capability=gpu.get("compute_capability"),
        ram_mb=_ram_mb(),
        cpu_count=os.cpu_count() or 1,
        free_disk_mb=_free_disk_mb(path or None),
        ffmpeg=ffmpeg_available(),
    )


def clear_hardware_cache() -> None:
    probe_hardware.cache_clear()


def resolve_device(requested: str, hw: Optional[HardwareInfo] = None) -> str:
    requested = (requested or "auto").lower()
    if requested in {"cpu", "cuda"}:
        return requested
    hw = hw or probe_hardware()
    return "cuda" if hw.cuda else "cpu"

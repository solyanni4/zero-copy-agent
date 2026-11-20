"""
Hardware Detection & Local LLM Recommendation Engine for zero-copy-agent.
Identifies host CPU, System RAM, GPU (NVIDIA, AMD, Intel, Apple Silicon),
VRAM, and evaluates Ollama / local runtime capabilities to recommend optimal models.
"""
import os
import sys
import shutil
import logging
import platform
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional

logger = logging.getLogger("zcao.hardware")


@dataclass
class HardwareProfile:
    gpu_name: str = "Интегрированная графика / CPU"
    gpu_vendor: str = "Unknown"  # nvidia, amd, intel, apple, cpu
    vram_gb: float = 0.0
    total_ram_gb: float = 0.0
    cpu_name: str = ""
    cpu_cores: int = 0
    ollama_online: bool = False
    ollama_models: List[str] = field(default_factory=list)
    display_footer: str = ""
    recommended_tier: str = "1.5B–3B"
    recommended_models: List[Dict[str, Any]] = field(default_factory=list)


def _detect_system_ram_gb() -> float:
    """Detects total physical system RAM in GB."""
    try:
        if sys.platform == "win32":
            # Fast detection via PowerShell / CIM
            cmd = ["powershell", "-NoProfile", "-Command", "[math]::round((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize / 1MB, 1)"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                # Handle comma/dot decimal separator
                val_str = res.stdout.strip().replace(",", ".")
                return float(val_str)
        elif sys.platform == "darwin":
            cmd = ["sysctl", "-n", "hw.memsize"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                return round(int(res.stdout.strip()) / (1024 ** 3), 1)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024 ** 2), 1)
    except Exception as e:
        logger.debug(f"RAM detection error: {e}")
    return 16.0  # Safe modern default


def _detect_cpu_info() -> tuple[str, int]:
    """Detects CPU model name and physical core count."""
    cpu_name = platform.processor() or "Multi-Core CPU"
    cores = os.cpu_count() or 4
    try:
        if sys.platform == "win32":
            cmd = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                cpu_name = res.stdout.strip()
        elif sys.platform == "darwin":
            cmd = ["sysctl", "-n", "machdep.cpu.brand_string"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                cpu_name = res.stdout.strip()
    except Exception:
        pass
    return cpu_name, cores


def _detect_nvidia_gpu() -> Optional[tuple[str, float]]:
    """Detects NVIDIA GPU via nvidia-smi if available."""
    try:
        cmd = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = first_line.split(",")
            if len(parts) >= 2:
                name = parts[0].strip()
                vram_mib = float(parts[1].strip())
                vram_gb = round(vram_mib / 1024, 1)
                return name, vram_gb
    except Exception:
        pass
    return None


def _detect_windows_gpu() -> tuple[str, str, float]:
    """Detects discrete or integrated GPU on Windows via WMI/CIM."""
    nvidia = _detect_nvidia_gpu()
    if nvidia:
        return nvidia[0], "nvidia", nvidia[1]

    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            import json
            data = json.loads(res.stdout)
            controllers = data if isinstance(data, list) else [data]

            best_name = "Интегрированная графика / CPU"
            best_vendor = "cpu"
            max_vram = 0.0

            for c in controllers:
                name = c.get("Name", "")
                raw_ram = c.get("AdapterRAM", 0) or 0
                vram_gb = round(raw_ram / (1024 ** 3), 1) if raw_ram > 0 else 0.0
                name_l = name.lower()

                if "nvidia" in name_l:
                    return name, "nvidia", vram_gb or 8.0
                elif "radeon" in name_l or "amd" in name_l:
                    if vram_gb > max_vram or best_vendor == "cpu":
                        best_name = name
                        best_vendor = "amd"
                        max_vram = max(vram_gb, max_vram)
                elif "intel" in name_l and best_vendor == "cpu":
                    best_name = name
                    best_vendor = "intel"
                    max_vram = max(vram_gb, max_vram)

            return best_name, best_vendor, max_vram
    except Exception as e:
        logger.debug(f"Windows GPU detection error: {e}")

    return "CPU-only (Интегрированная графика)", "cpu", 0.0


def check_ollama_status() -> tuple[bool, List[str]]:
    """Checks if local Ollama daemon is reachable and lists its installed models."""
    try:
        import urllib.request
        import json
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "zero-copy-agent"})
        with urllib.request.urlopen(req, timeout=1.2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return True, models
    except Exception:
        pass
    return False, []


def generate_model_recommendations(vram_gb: float, ram_gb: float, gpu_vendor: str) -> tuple[str, List[Dict[str, Any]]]:
    """Generates tailor-made local LLM suggestions based on physical hardware specs."""
    recs = []

    if vram_gb >= 22.0 or (gpu_vendor == "apple" and ram_gb >= 36.0):
        tier = "32B (Тяжелая архитектура и аудит)"
        recs.append({
            "name": "Qwen2.5-Coder 32B (Q4_K_M)",
            "size": "19 ГБ",
            "req": ">= 24 ГБ VRAM",
            "role": "Глубокий рефакторинг сложных систем и полный аудит кода",
            "command": "ollama run qwen2.5-coder:32b",
            "is_best": True
        })
        recs.append({
            "name": "Qwen2.5-Coder 14B (Q5_K_M)",
            "size": "9.5 ГБ",
            "req": "12–16 ГБ VRAM",
            "role": "Молниеносный генератор функций с 32k контекстом",
            "command": "ollama run qwen2.5-coder:14b",
            "is_best": False
        })
    elif vram_gb >= 11.0 or (gpu_vendor == "apple" and ram_gb >= 18.0):
        # Fits RTX 4070 (12GB), RTX 3060 (12GB), RTX 4080 (16GB)
        tier = "14B (Золотой стандарт для кодинга)"
        recs.append({
            "name": "Qwen2.5-Coder 14B (Q4_K_M)",
            "size": "8.9 ГБ",
            "req": "10–12 ГБ VRAM",
            "role": "Оптимальный баланс: пишет чистый код, тесты и понимает сложный контекст",
            "command": "ollama run qwen2.5-coder:14b",
            "is_best": True
        })
        recs.append({
            "name": "DeepSeek-Coder 6.7B",
            "size": "4.2 ГБ",
            "req": "6 ГБ VRAM",
            "role": "Сверхбыстрый исполнитель для инлайн-автокомплита в IDE",
            "command": "ollama run deepseek-coder:6.7b",
            "is_best": False
        })
        recs.append({
            "name": "Qwen2.5-Coder 7B",
            "size": "4.7 ГБ",
            "req": "6–8 ГБ VRAM",
            "role": "Легковесный кодер с низким latency",
            "command": "ollama run qwen2.5-coder:7b",
            "is_best": False
        })
    elif vram_gb >= 5.5:
        # 6GB - 8GB GPUs (RTX 3050, 4060, RX 6600, GTX 1660 Ti)
        tier = "7B–8B (Комфортная локальная работа)"
        recs.append({
            "name": "Qwen2.5-Coder 7B (Q4_K_M)",
            "size": "4.7 ГБ",
            "req": "6–8 ГБ VRAM",
            "role": "Высокая точность Python/TypeScript, идеален для повседневных задач",
            "command": "ollama run qwen2.5-coder:7b",
            "is_best": True
        })
        recs.append({
            "name": "Qwen2.5-Coder 3B",
            "size": "2.1 ГБ",
            "req": "3–4 ГБ VRAM",
            "role": "Мгновенные подсказки и генерация мелких функций",
            "command": "ollama run qwen2.5-coder:3b",
            "is_best": False
        })
    else:
        # <= 4GB VRAM or CPU-only with system RAM
        tier = "1.5B–3B (Легковесный режим / CPU)"
        recs.append({
            "name": "Qwen2.5-Coder 1.5B (Q4_K_M)",
            "size": "1.1 ГБ",
            "req": "2 ГБ RAM/VRAM",
            "role": "Работает на любом ноутбуке и слабом железе, быстрый автокомплит",
            "command": "ollama run qwen2.5-coder:1.5b",
            "is_best": True
        })
        recs.append({
            "name": "Qwen2.5-Coder 3B",
            "size": "2.1 ГБ",
            "req": "4 ГБ RAM",
            "role": "Базовые задачи кодогенерации без зависаний",
            "command": "ollama run qwen2.5-coder:3b",
            "is_best": False
        })

    return tier, recs


def get_hardware_profile() -> HardwareProfile:
    """Collects full host hardware profile and model sizing suggestions."""
    gpu_name, gpu_vendor, vram_gb = _detect_windows_gpu() if sys.platform == "win32" else ("CPU-only", "cpu", 0.0)
    ram_gb = _detect_system_ram_gb()
    cpu_name, cpu_cores = _detect_cpu_info()
    ollama_online, ollama_models = check_ollama_status()

    tier, recs = generate_model_recommendations(vram_gb, ram_gb, gpu_vendor)

    # Clean display footer string for UI
    runtime_str = f"Ollama ({len(ollama_models)} мод.)" if ollama_online else "Ollama (офлайн)"
    if vram_gb > 0:
        display_footer = f"{gpu_name} ({vram_gb} ГБ) · {runtime_str}"
    else:
        display_footer = f"{gpu_name} ({ram_gb} ГБ RAM) · {runtime_str}"

    return HardwareProfile(
        gpu_name=gpu_name,
        gpu_vendor=gpu_vendor,
        vram_gb=vram_gb,
        total_ram_gb=ram_gb,
        cpu_name=cpu_name,
        cpu_cores=cpu_cores,
        ollama_online=ollama_online,
        ollama_models=ollama_models,
        display_footer=display_footer,
        recommended_tier=tier,
        recommended_models=recs
    )

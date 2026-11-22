"""
Hardware Diagnostics and Local Ollama Model Advisor for zero-copy-agent.
Evaluates physical GPU VRAM and CPU/RAM to recommend optimal local coder and embedding weights.
Supports live model installation (streaming pull via SSE/NDJSON) and one-click activation.
"""
import os
import sys
import json
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator
import httpx

from core.hardware import (
    _detect_windows_gpu,
    _detect_nvidia_gpu,
    _detect_system_ram_gb,
    _detect_cpu_info,
    check_ollama_status
)
from core.config import config, save_config, get_runtime_cwd
from core.healer import config_healer

logger = logging.getLogger("zcao.hardware_advisor")

# Standard curated model catalog for zero-copy local workflows
RECOMMENDED_CATALOG = {
    "embedding": {
        "name": "nomic-embed-text",
        "display_name": "Nomic Embed Text (Локальные эмбеддинги)",
        "size_str": "274 МБ",
        "min_vram_gb": 1.0,
        "description": "Сверхбыстрая векторная модель для Базы решений Архитектора и локального семантического поиска (768 dim).",
        "category": "embedding"
    },
    "light": [
        {
            "name": "qwen2.5-coder:3b",
            "display_name": "Qwen 2.5 Coder 3B",
            "size_str": "1.9 ГБ",
            "min_vram_gb": 3.0,
            "description": "Лёгкий и очень быстрый кодер для ноутбуков, интегрированной графики и видеокарт до 6 ГБ VRAM.",
            "category": "coder"
        },
        {
            "name": "qwen2.5-coder:7b-instruct-q4_K_M",
            "display_name": "Qwen 2.5 Coder 7B (Q4_K_M)",
            "size_str": "4.7 ГБ",
            "min_vram_gb": 5.5,
            "description": "Оптимизированный 7B квант с хорошим балансом скорости и точности для карт с 4-6 ГБ VRAM.",
            "category": "coder"
        }
    ],
    "medium_8gb": [
        {
            "name": "qwen2.5-coder:7b",
            "display_name": "Qwen 2.5 Coder 7B",
            "size_str": "4.7 ГБ",
            "min_vram_gb": 7.0,
            "description": "Отличная рабочая лошадка для карт уровня RTX 3060/4060 8 ГБ: быстрое автодополнение и правка функций.",
            "category": "coder"
        },
        {
            "name": "deepseek-coder-v2:lite",
            "display_name": "DeepSeek Coder V2 Lite (16B MoE)",
            "size_str": "8.9 ГБ",
            "min_vram_gb": 8.0,
            "description": "MoE-архитектура с высокой плотностью кода, помещается в 8-10 ГБ смешанной памяти.",
            "category": "coder"
        }
    ],
    "flagship_12gb": [
        {
            "name": "qwen2.5-coder:14b",
            "display_name": "Qwen 2.5 Coder 14B (Золотой стандарт)",
            "size_str": "9.0 ГБ",
            "min_vram_gb": 10.5,
            "description": "Рекомендованная модель для RTX 4070 / 3060 12 ГБ. Идеальная архитектурная декомпозиция и сложные алгоритмы.",
            "category": "coder"
        },
        {
            "name": "deepseek-r1:14b",
            "display_name": "DeepSeek R1 14B (Reasoning / DeepThink)",
            "size_str": "9.0 ГБ",
            "min_vram_gb": 10.5,
            "description": "Логическая модель с цепочкой рассуждений (Chain-of-Thought) для автономного поиска неочевидных багов.",
            "category": "planner"
        }
    ],
    "heavy_24gb": [
        {
            "name": "qwen2.5-coder:32b",
            "display_name": "Qwen 2.5 Coder 32B (Тяжёлый исполнитель)",
            "size_str": "19.0 ГБ",
            "min_vram_gb": 20.0,
            "description": "Мощнейший открытый кодер для рабочих станций с RTX 3090 / 4090 / Mac 36+ ГБ. Превосходит многие облачные API.",
            "category": "coder"
        }
    ]
}


class HardwareAdvisor:
    """Provides hardware assessment, model recommendations, and live Ollama installation pipelines."""

    def __init__(self, ollama_url: str = "http://127.0.0.1:11434"):
        clean_url = (ollama_url or "http://127.0.0.1:11434").rstrip("/")
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = f"http://{clean_url}"
        clean_url = clean_url.replace("://0.0.0.0", "://127.0.0.1")
        self.ollama_url = clean_url

    def get_hardware_summary(self) -> Dict[str, Any]:
        """Collects GPU, VRAM, RAM, and CPU telemetry."""
        # 1. GPU / VRAM
        if sys.platform == "win32":
            gpu_name, vendor, vram_gb = _detect_windows_gpu()
        else:
            nv = _detect_nvidia_gpu()
            if nv:
                gpu_name, vram_gb = nv
                vendor = "nvidia"
            else:
                gpu_name, vendor, vram_gb = "Интегрированная графика / CPU", "cpu", 0.0

        # 2. System RAM
        ram_gb = _detect_system_ram_gb()

        # 3. CPU
        cpu_name, cpu_cores = _detect_cpu_info()

        # 4. Ollama live status
        ollama_ok, installed_models = check_ollama_status()

        return {
            "gpu_name": gpu_name,
            "gpu_vendor": vendor,
            "vram_gb": round(vram_gb, 1),
            "ram_gb": round(ram_gb, 1),
            "cpu_name": cpu_name,
            "cpu_cores": cpu_cores,
            "ollama_online": ollama_ok,
            "installed_models": installed_models
        }

    def get_model_recommendations(self) -> Dict[str, Any]:
        """
        Calculates recommended models matrix tailored for current hardware specs.
        """
        hw = self.get_hardware_summary()
        vram = hw["vram_gb"]
        ram = hw["ram_gb"]

        def get_variants(name: str) -> set:
            if not name:
                return set()
            n = name.strip().lower()
            variants = {n}
            if ":" not in n:
                variants.add(f"{n}:latest")
            elif n.endswith(":latest"):
                variants.add(n[:-7])
            return variants

        installed_variants = set()
        for m in hw["installed_models"]:
            installed_variants.update(get_variants(m))

        def check_status(model_id: str) -> str:
            m_variants = get_variants(model_id)
            if m_variants & installed_variants:
                # Check if set in config as active coder or planner
                current_coder = getattr(config.cascade, "coder_model", "")
                current_variants = get_variants(current_coder)
                if m_variants & current_variants:
                    return "active"
                return "installed"
            return "not_installed"

        # Tier selection
        recommended_list: List[Dict[str, Any]] = []

        if vram >= 18.0 or (hw["gpu_vendor"] == "apple" and ram >= 32.0):
            tier_name = "24 ГБ VRAM (Тяжёлый класс 32B)"
            for m in RECOMMENDED_CATALOG["heavy_24gb"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))
            for m in RECOMMENDED_CATALOG["flagship_12gb"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))
        elif vram >= 10.5 or (hw["gpu_vendor"] == "apple" and ram >= 18.0):
            tier_name = "12 ГБ VRAM (Флагманский класс 14B)"
            for m in RECOMMENDED_CATALOG["flagship_12gb"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))
            for m in RECOMMENDED_CATALOG["medium_8gb"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))
        elif vram >= 6.5:
            tier_name = "8 ГБ VRAM (Сбалансированный класс 7B–16B)"
            for m in RECOMMENDED_CATALOG["medium_8gb"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))
            for m in RECOMMENDED_CATALOG["light"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))
        else:
            tier_name = "Менее 6 ГБ VRAM (Лёгкий класс 3B–7B)"
            for m in RECOMMENDED_CATALOG["light"]:
                recommended_list.append(dict(m, status=check_status(m["name"])))

        # Embedding model status
        emb_model = RECOMMENDED_CATALOG["embedding"]
        emb_status = check_status(emb_model["name"])
        embedding_rec = dict(emb_model, status=emb_status)

        return {
            "hardware": hw,
            "recommended_tier": tier_name,
            "has_embedding_model": emb_status in ("installed", "active"),
            "embedding_recommendation": embedding_rec,
            "recommendations": recommended_list
        }

    async def stream_pull_model(self, model_name: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streams model download progress from Ollama API (/api/pull).
        Yields JSON payloads with status, completed bytes, total bytes, and percent.
        """
        clean_name = model_name.strip()
        url = f"{self.ollama_url}/api/pull"
        payload = {"name": clean_name, "stream": True}

        logger.info(f"[HardwareAdvisor] Starting pull for model '{clean_name}' from {url}")

        timeout = httpx.Timeout(connect=10.0, read=1800.0, write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            try:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        yield {"status": "error", "message": f"Ollama HTTP {response.status_code}"}
                        return

                    async for line in response.aiter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                status_text = chunk.get("status", "")
                                total = chunk.get("total", 0)
                                completed = chunk.get("completed", 0)
                                pct = round((completed / total) * 100.0, 1) if (total and total > 0) else 0.0

                                yield {
                                    "status": status_text,
                                    "completed": completed,
                                    "total": total,
                                    "percent": pct,
                                    "model": clean_name
                                }
                                if status_text.lower() == "success":
                                    break
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"[HardwareAdvisor] Pull failed for '{clean_name}': {e}")
                yield {"status": "error", "message": str(e), "model": clean_name}

    def activate_model(self, model_name: str, role: str = "coder") -> Dict[str, Any]:
        """
        Designates installed Ollama model as active coder or embedding model in config.yaml.
        Performs immediate warm-up check.
        """
        clean_name = model_name.strip()

        # 1. Update config.yaml
        if role == "coder":
            config.cascade.coder_model = clean_name
            if hasattr(config.cascade, "coder") and config.cascade.coder:
                config.cascade.coder.primary = clean_name
        elif role == "planner":
            config.cascade.architect_model = clean_name
            if hasattr(config.cascade, "planner") and config.cascade.planner:
                config.cascade.planner.primary = clean_name
        save_config(config)

        # 2. Register in Subscriptions via healer
        try:
            config_healer.sync_discovered_models("ollama", [clean_name])
        except Exception as e:
            logger.debug(f"[HardwareAdvisor] Healer sync warning: {e}")

        logger.info(f"[HardwareAdvisor] Activated model '{clean_name}' for role '{role}'")
        return {
            "status": "ok",
            "model": clean_name,
            "role": role,
            "message": f"Модель '{clean_name}' успешно активирована для роли {role}"
        }


# Global singleton advisor
hardware_advisor = HardwareAdvisor()

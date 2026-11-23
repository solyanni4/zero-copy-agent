"""
Orchestration Presets Engine for zero-copy-agent.
Manages user-customizable orchestration profiles alongside 3 built-in templates:
- Авто (auto): Intelligent GPU + Cloud workload balancer.
- Базовый (basic): Fast lightweight analysis and small edits without heavy review.
- МЫСЛИТЕЛЬ (thinker): Maximum power mode: Solutions RAG + DAG decomposition + multi-model cross-review + AST gates.
"""
import os
import sys
import copy
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

from core.config import get_runtime_cwd

logger = logging.getLogger("zcao.presets")


class OrchestrationPreset(BaseModel):
    id: str
    name: str
    description: str
    is_builtin: bool = False
    planner_model: str = "gemini-4-neon"
    architect_models: List[str] = Field(default_factory=lambda: ["gemini-4-neon"])
    coder_model: str = "qwen2.5-coder:14b"
    reviewer_models: List[str] = Field(default_factory=lambda: ["gemini-4-neon"])
    use_solutions_db: bool = True
    use_dag_planner: bool = True
    use_prompt_compression: bool = True
    cross_review_depth: int = 1
    strict_ast_test_gates: bool = True
    parallel_review: bool = False


BUILTIN_PRESETS: List[OrchestrationPreset] = [
    OrchestrationPreset(
        id="auto",
        name="Авто",
        description="Умный балансировщик: оценивает сложность задачи и распределяет нагрузку между локальным GPU и пулом облачных квот.",
        is_builtin=True,
        planner_model="gemini-4-neon",
        architect_models=["gemini-4-neon"],
        coder_model="qwen2.5-coder:14b",
        reviewer_models=["gemini-4-neon"],
        use_solutions_db=True,
        use_dag_planner=False,
        use_prompt_compression=True,
        cross_review_depth=1,
        strict_ast_test_gates=True,
        parallel_review=False
    ),
    OrchestrationPreset(
        id="basic",
        name="Базовый",
        description="Быстрый универсальный режим для ответов на вопросы и точечных правок кода: короткая цепочка без глубокой декомпозиции и ревью.",
        is_builtin=True,
        planner_model="gemini-4-neon",
        architect_models=["gemini-4-neon"],
        coder_model="qwen2.5-coder:14b",
        reviewer_models=["gemini-4-neon"],
        use_solutions_db=False,
        use_dag_planner=False,
        use_prompt_compression=True,
        cross_review_depth=0,
        strict_ast_test_gates=False,
        parallel_review=False
    ),
    OrchestrationPreset(
        id="thinker",
        name="МЫСЛИТЕЛЬ",
        description="Режим максимальной мощности: подключает Базу решений Архитектора, полную DAG-декомпозицию, кросс-ревью всеми облачными моделями и строгие AST-гейты.",
        is_builtin=True,
        planner_model="deepseek-reasoner",
        architect_models=["deepseek-reasoner", "gemini-4-neon"],
        coder_model="qwen2.5-coder:14b",
        reviewer_models=["gemini-4-neon", "claude-3-7-sonnet", "grok-3"],
        use_solutions_db=True,
        use_dag_planner=True,
        use_prompt_compression=True,
        cross_review_depth=2,
        strict_ast_test_gates=True,
        parallel_review=True
    ),
]


class PresetsManager:
    """Manages loading, persisting, and activating orchestration presets in presets.yaml."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (get_runtime_cwd() / "presets.yaml")
        self._active_preset_id: str = "auto"
        self._presets: Dict[str, OrchestrationPreset] = {}
        self.load()

    def load(self):
        """Loads presets from presets.yaml or initializes starter built-in templates."""
        builtins_map = {p.id: p for p in BUILTIN_PRESETS}

        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self._active_preset_id = data.get("active_preset", "auto")
                loaded_list = data.get("presets", [])

                loaded_presets = {}
                for item in loaded_list:
                    try:
                        p = OrchestrationPreset(**item)
                        # Keep is_builtin flag if it's one of standard 3
                        if p.id in builtins_map:
                            p.is_builtin = True
                        loaded_presets[p.id] = p
                    except Exception as parse_e:
                        logger.warning(f"[Presets] Error parsing preset item: {parse_e}")

                # Ensure all 3 built-ins exist
                for bid, bpreset in builtins_map.items():
                    if bid not in loaded_presets:
                        loaded_presets[bid] = copy.deepcopy(bpreset)

                self._presets = loaded_presets
                return
            except Exception as e:
                logger.error(f"[Presets] Error loading {self.config_path}: {e}. Reverting to defaults.")

        # Default initialization
        self._presets = {p.id: copy.deepcopy(p) for p in BUILTIN_PRESETS}
        self._active_preset_id = "auto"
        self.save()

    def save(self):
        """Saves presets atomically to presets.yaml."""
        presets_data = [
            p.model_dump() if hasattr(p, "model_dump") else p.dict()
            for p in self._presets.values()
        ]
        payload = {
            "version": "1.6.0",
            "active_preset": self._active_preset_id,
            "presets": presets_data
        }

        tmp_path = self.config_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.dump(payload, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            if self.config_path.exists():
                self.config_path.unlink()
            tmp_path.rename(self.config_path)
            logger.info(f"[Presets] Successfully saved {len(self._presets)} presets to {self.config_path}")
        except Exception as e:
            logger.error(f"[Presets] Failed to save {self.config_path}: {e}")
            if tmp_path.exists():
                tmp_path.unlink()

    def list_presets(self) -> List[Dict[str, Any]]:
        """Returns list of presets as dictionaries."""
        return [
            (p.model_dump() if hasattr(p, "model_dump") else p.dict())
            for p in self._presets.values()
        ]

    def get_preset(self, preset_id: str) -> Optional[OrchestrationPreset]:
        return self._presets.get(preset_id)

    def get_active_preset(self) -> OrchestrationPreset:
        return self._presets.get(self._active_preset_id) or self._presets.get("auto") or BUILTIN_PRESETS[0]

    def set_active_preset(self, preset_id: str) -> bool:
        if preset_id not in self._presets:
            return False
        self._active_preset_id = preset_id
        self.save()
        logger.info(f"[Presets] Switched active preset to '{preset_id}'")
        return True

    def create_or_update_preset(self, preset_data: Dict[str, Any]) -> Tuple[OrchestrationPreset, bool]:
        """Creates or updates a preset. Built-in presets cannot be renamed or deleted."""
        pid = preset_data.get("id", "").strip().lower()
        if not pid:
            raise ValueError("ID пресета не может быть пустым")

        is_new = pid not in self._presets
        existing = self._presets.get(pid)
        is_builtin = existing.is_builtin if existing else False

        # Protect built-in status
        preset_data["is_builtin"] = is_builtin

        preset_obj = OrchestrationPreset(**preset_data)
        self._presets[pid] = preset_obj
        self.save()
        return preset_obj, is_new

    def delete_preset(self, preset_id: str) -> bool:
        """Deletes preset if it is not built-in."""
        preset = self._presets.get(preset_id)
        if not preset:
            return False
        if preset.is_builtin:
            raise ValueError(f"Встроенный системный пресет '{preset.name}' защищён от удаления")

        del self._presets[preset_id]
        if self._active_preset_id == preset_id:
            self._active_preset_id = "auto"
        self.save()
        return True

    def reset_defaults(self):
        """Restores 3 built-in templates without destroying user custom presets."""
        for bpreset in BUILTIN_PRESETS:
            self._presets[bpreset.id] = copy.deepcopy(bpreset)
        self.save()
        logger.info("[Presets] Reset built-in templates to default specifications.")


# Global singleton manager
presets_manager = PresetsManager()

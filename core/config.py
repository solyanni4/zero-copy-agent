import os
import sys
from pathlib import Path
from typing import List, Optional, Dict
import yaml
from pydantic import BaseModel, Field

def get_runtime_cwd() -> Path:
    """
    Returns the persistent runtime directory for zero-copy-agent.
    For frozen PyInstaller binaries, returns the directory containing the executable.
    CRITICAL: Never returns temporary extracted directory _MEIPASS.
    For source execution, returns repository root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_config_path() -> Path:
    env_path = os.getenv("CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # 1. Custom config in current working directory
    cwd_path = Path.cwd() / "config.yaml"
    if cwd_path.exists():
        return cwd_path

    # 2. Config next to executable (for frozen binaries)
    if getattr(sys, "frozen", False):
        exe_dir_path = Path(sys.executable).parent / "config.yaml"
        if exe_dir_path.exists():
            return exe_dir_path
        
        # 3. Bundled config extracted by PyInstaller
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundle_path = Path(meipass) / "config.yaml"
            if bundle_path.exists():
                return bundle_path

    return Path("config.yaml")

CONFIG_PATH = get_config_path()

class ModelConfig(BaseModel):
    id: str
    name: str
    provider: str

class ServerConfig(BaseModel):
    host: str = Field(default_factory=lambda: os.getenv("ZCA_HOST", "127.0.0.1"))
    port: int = 8000
    workers: int = 1

class BrowserConfig(BaseModel):
    headless: bool = True
    user_data_dir: str = "./browser_profiles"
    provider: str = "gemini"
    timeout_seconds: int = 60
    max_active_contexts: int = 3

class ModelsGroupConfig(BaseModel):
    default: str = "gemini-advanced"
    available: List[ModelConfig] = Field(default_factory=list)

class ObsidianConfig(BaseModel):
    enabled: bool = False
    vault_path: str = ""

class UxConfig(BaseModel):
    inject_model_signature: bool = True
    header_attribution: bool = True
    hide_inactive_models: bool = True
    display_names_override: Dict[str, str] = Field(default_factory=dict)

class CascadeConfig(BaseModel):
    max_rework_iterations: int = 2
    enable_local_gate: bool = True
    enable_architect_audit: bool = True
    architect_model: str = "gemini-4-neon"
    coder_model: str = "qwen2.5-coder:14b"
    auditor_model: str = "gemini-4-neon"
    executor_model: Optional[str] = None
    reviewer_model: Optional[str] = None
    hard_gate_cmd: str = "python -m py_compile"
    mode: str = "auto"

class AppConfig(BaseModel):
    version: str = "1.0.0"
    server: ServerConfig = Field(default_factory=ServerConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    models: ModelsGroupConfig = Field(default_factory=ModelsGroupConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    ux: UxConfig = Field(default_factory=UxConfig)
    cascade: CascadeConfig = Field(default_factory=CascadeConfig)

def load_config() -> AppConfig:
    cfg_path = get_config_path()
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return AppConfig(**data)
        except Exception as e:
            print(f"[WARN] Error loading {cfg_path}: {e}. Falling back to default settings.")
    return AppConfig()

def save_config(cfg: AppConfig) -> bool:
    cfg_path = get_config_path()
    try:
        data = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg.dict()
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save config to {cfg_path}: {e}")
        return False

config = load_config()

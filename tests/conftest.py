import pytest
from pathlib import Path

@pytest.fixture(autouse=True, scope="session")
def protect_repo_configs():
    """Guarantees that test execution does not leave modified config files in the working tree."""
    subscriptions_path = Path("subscriptions.yaml")
    config_path = Path("config.yaml")

    sub_bytes = subscriptions_path.read_bytes() if subscriptions_path.exists() else None
    cfg_bytes = config_path.read_bytes() if config_path.exists() else None

    yield

    if sub_bytes is not None and subscriptions_path.exists():
        subscriptions_path.write_bytes(sub_bytes)
    if cfg_bytes is not None and config_path.exists():
        config_path.write_bytes(cfg_bytes)

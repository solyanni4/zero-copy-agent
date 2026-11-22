"""
Unit tests for core/hardware_advisor.py (Diagnostics, recommendations and matrix matching).
"""
import pytest
from core.hardware_advisor import HardwareAdvisor, RECOMMENDED_CATALOG


@pytest.fixture
def advisor():
    return HardwareAdvisor()


def test_hardware_summary(advisor):
    summary = advisor.get_hardware_summary()
    assert "gpu_name" in summary
    assert "vram_gb" in summary
    assert "ram_gb" in summary
    assert "cpu_name" in summary
    assert "ollama_online" in summary
    assert isinstance(summary["installed_models"], list)


def test_model_recommendations_structure(advisor):
    recs = advisor.get_model_recommendations()
    assert "hardware" in recs
    assert "recommended_tier" in recs
    assert "has_embedding_model" in recs
    assert "embedding_recommendation" in recs
    assert "recommendations" in recs
    assert len(recs["recommendations"]) >= 1

    # Embedding model is nomic-embed-text
    emb = recs["embedding_recommendation"]
    assert emb["name"] == "nomic-embed-text"


def test_catalog_integrity():
    assert "embedding" in RECOMMENDED_CATALOG
    assert "light" in RECOMMENDED_CATALOG
    assert "medium_8gb" in RECOMMENDED_CATALOG
    assert "flagship_12gb" in RECOMMENDED_CATALOG
    assert "heavy_24gb" in RECOMMENDED_CATALOG

    for tier, models in RECOMMENDED_CATALOG.items():
        if isinstance(models, list):
            for m in models:
                assert "name" in m
                assert "display_name" in m
                assert "min_vram_gb" in m

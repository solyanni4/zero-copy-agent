import pytest
from core.compression import ContextCompressor, compressor

def test_l1_whitespace_and_empty_comments():
    raw = """
    def calculate():
        #
        x = 10
        
        
        
        return x
    """
    compressed = ContextCompressor.compress_l1(raw)
    assert "#" not in compressed
    assert "\n\n\n" not in compressed

def test_l2_cyrillic_filler_compaction():
    text = "Пожалуйста, будь добр, напиши мне функцию для сортировки списка чисел. Заранее большое спасибо!"
    compressed, applied = ContextCompressor.compress_l2(text)
    assert applied is True
    assert "Пожалуйста" not in compressed
    assert "будь добр" not in compressed
    assert "Заранее большое спасибо" not in compressed
    assert "напиши мне функцию для сортировки списка чисел" in compressed

def test_compressor_pipeline():
    text = "Помоги мне пожалуйста написать класс на Python для работы с REST API.\n\n\n\n#\n"
    res, orig_t, comp_t = compressor.compress_text(text)
    assert "Помоги мне пожалуйста" not in res
    assert comp_t < orig_t

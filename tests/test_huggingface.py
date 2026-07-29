"""Hugging Face tool formatting helpers (offline — no network)."""
from tools.huggingface import _fmt_count, _model_line


def test_fmt_count():
    assert _fmt_count(1_384_975) == "1.4M"
    assert _fmt_count(2500) == "2.5K"
    assert _fmt_count(42) == "42"
    assert _fmt_count(None) == "—"


def test_model_line_shape():
    line = _model_line({"id": "black-forest-labs/FLUX.1-dev", "downloads": 1_200_000,
                        "likes": 3400, "pipeline_tag": "text-to-image"})
    assert "black-forest-labs/FLUX.1-dev" in line
    assert "text-to-image" in line
    assert "huggingface.co/black-forest-labs/FLUX.1-dev" in line
    assert "1.2M" in line

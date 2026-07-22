"""text_looks_successful — the classifier driving pass/fail in the smoke suite."""
import pytest

from core.result_status import text_looks_successful


@pytest.mark.parametrize("text", [
    "## Jellyfin Search: 'x'\n\n**Movie**",
    "OK",
    "✓ Added 'Foo' to Sonarr. ID: 5",
    "No results found for 'zzz'",  # empty-but-valid result is still "successful"
])
def test_successful_outputs(text):
    assert text_looks_successful(text) is True


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "Error: Jellyfin not configured.",
    "1 validation error for Input",
    "field required",
    "Traceback (most recent call last):",
    "TypeError: bad",
])
def test_failure_outputs(text):
    assert text_looks_successful(text) is False


def test_none_is_not_successful():
    assert text_looks_successful(None) is False

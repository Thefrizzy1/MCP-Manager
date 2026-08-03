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


@pytest.mark.parametrize("text", [
    # Live content that merely mentions error words in its BODY is still a working
    # result — only error-shaped output at the head counts as a failure.
    "## r/homelab — hot\n\nSomeone hit a TypeError in their backup script.",
    "Search results:\n1. How to fix a Traceback in Python",
    "## OpenStreetMap\n\nA validation error message is what the article discusses.",
])
def test_error_words_in_the_body_do_not_fail_a_working_tool(text):
    assert text_looks_successful(text) is True


def test_error_shaped_head_still_fails():
    """The head is still scanned, so a real error on the first line is caught."""
    assert text_looks_successful("TypeError: bad thing\n  at line 3") is False
    assert text_looks_successful("2 validation errors for Input\ntitle\n  field required") is False

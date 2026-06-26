"""FILESYSTEM_ALLOWED_PATHS parsing — tolerate the Python-list-literal mistake."""
from config import parse_csv_paths


def test_plain_csv():
    assert parse_csv_paths("/a,/b,/c") == ["/a", "/b", "/c"]


def test_csv_with_spaces():
    assert parse_csv_paths(" /a , /b ") == ["/a", "/b"]


def test_python_list_literal_single_quotes():
    # The exact shape found in the user's .env.
    raw = "['/01_Offene_Jobs', '/Hausatredies', '/Ablage', '/Backup']"
    assert parse_csv_paths(raw) == ["/01_Offene_Jobs", "/Hausatredies", "/Ablage", "/Backup"]


def test_json_list_double_quotes():
    assert parse_csv_paths('["/a", "/b"]') == ["/a", "/b"]


def test_empty_and_blank():
    assert parse_csv_paths("") == []
    assert parse_csv_paths("   ") == []
    assert parse_csv_paths(",, ,") == []

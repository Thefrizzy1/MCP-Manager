"""Path-confinement guard — the layer where the sibling-prefix bug lived."""
import os

from core.path_guard import is_within, is_within_any


def _r(p):
    # Compare against realpath so tests are CWD/symlink independent.
    return os.path.realpath(p)


def test_exact_root_is_allowed(tmp_path):
    root = str(tmp_path)
    assert is_within(root, root)


def test_child_is_allowed(tmp_path):
    root = str(tmp_path)
    child = os.path.join(root, "sub", "file.txt")
    assert is_within(child, root)


def test_sibling_prefix_is_rejected(tmp_path):
    # The classic bug: "/Ablage_secret" must NOT match root "/Ablage".
    root = tmp_path / "Ablage"
    sibling = tmp_path / "Ablage_secret"
    root.mkdir()
    sibling.mkdir()
    assert not is_within(str(sibling), str(root))
    assert not is_within(str(sibling / "x.txt"), str(root))


def test_parent_traversal_is_rejected(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    escape = os.path.join(str(root), "..", "etc", "passwd")
    assert not is_within(escape, str(root))


def test_within_any_matches_one_of_many(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    roots = [str(a), str(b)]
    assert is_within_any(str(b / "deep" / "f"), roots)
    assert not is_within_any(str(tmp_path / "c" / "f"), roots)


def test_within_any_ignores_empty_roots(tmp_path):
    root = str(tmp_path)
    assert is_within_any(root, ["", root, None]) is True
    assert is_within_any("/nope", ["", None]) is False

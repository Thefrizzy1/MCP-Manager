"""The agent operating manual — rendering + injection into the command."""
from core.agent_skill import render_skill
from core.agent_runner import build_agent_cmd


def test_skill_names_the_filesystem_library_and_fallback():
    s = render_skill({"output_mode": "filesystem", "fs_library_path": "/data/library"})
    assert "/data/library" in s
    assert "db_write_note" in s          # the always-writable fallback
    assert "fs_read_file" in s
    assert "research library" in s.lower()


def test_skill_names_the_obsidian_library():
    s = render_skill({"output_mode": "obsidian", "obsidian_folder": "research"})
    assert "research" in s
    assert "obsidian_get_note" in s


def test_build_cmd_appends_the_skill_as_system_prompt():
    cmd = build_agent_cmd("do it", {"skip_permissions": False}, system_prompt="MANUAL")
    assert "--append-system-prompt" in cmd
    i = cmd.index("--append-system-prompt")
    assert cmd[i + 1] == "MANUAL"
    # the prompt must still be the trailing positional after --
    assert cmd[-2] == "--" and cmd[-1] == "do it"


def test_build_cmd_without_skill_is_unchanged():
    cmd = build_agent_cmd("do it", {"skip_permissions": False})
    assert "--append-system-prompt" not in cmd

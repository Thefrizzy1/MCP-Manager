"""Teleprompter: what is read out, what can be uploaded, and the remote link."""
import asyncio
import io

import pytest

from core import prompter as P


def test_markdown_marks_are_not_read_out():
    s = P.sections("---\na: b\n---\n# Intro\n**Hey** it's _me_, see [this](http://x) snake_case_name 3*4*5\n\n- one\n\n---\n## Part `two`\n[HOLD UP CARD]\n> quote")
    assert [x["title"] for x in s] == ["Intro", "Part two"]
    assert s[0]["text"] == "Hey it's me, see this snake_case_name 3*4*5\n\none"
    assert s[1]["text"] == "[HOLD UP CARD]\nquote"


def test_pdf_lines_are_joined_back_into_sentences():
    t = P.unwrap_pdf("===== Page 1 / 2 =====\nThis is a long line that the page broke in the\nmiddle of a sen-\ntence. Done.\n3\n\n===== Page 2 / 2 =====\nNext one.\nThen this.")
    assert "broke in the middle of a sentence. Done." in t
    assert "Next one.\nThen this." in t and "Page" not in t and "3" not in t


def test_uploads_are_kept_listed_and_removed(tmp_path):
    row = P.save_upload(tmp_path, "../My script.md", b"# Hi\nhello", "20261125_X")
    assert row["name"] == "My script.md" and "text" not in row
    P.set_current(tmp_path, f"upload:{row['id']}")
    assert [u["id"] for u in P.uploads(tmp_path)] == [row["id"]]         # current.json is not a script
    assert P.upload(tmp_path, row["id"])["text"] == "# Hi\nhello"
    assert P.remove_upload(tmp_path, row["id"]) and not P.remove_upload(tmp_path, "../../x")
    for bad in ("", "../current", row["id"]):
        with pytest.raises(KeyError):
            P.upload(tmp_path, bad)
    for name, data in (("x.png", b"\x89PNG"), ("x.md", b"  ")):
        with pytest.raises(ValueError):
            P.save_upload(tmp_path, name, data)


def test_a_picture_pdf_is_refused_with_the_reason(tmp_path):
    from pypdf import PdfWriter
    w, buf = PdfWriter(), io.BytesIO()
    w.add_blank_page(200, 200)
    w.write(buf)
    with pytest.raises(ValueError, match="scan"):
        P.save_upload(tmp_path, "a.pdf", buf.getvalue())


def test_a_press_reaches_a_waiting_screen_and_an_old_one_does_not_fire():
    async def go():
        link = P.Link()
        start = (await link.wait(-1))["seq"]
        waiting = asyncio.create_task(link.wait(start, 5))
        await asyncio.sleep(0.01)
        link.send("toggle")
        got = await waiting
        assert [c["cmd"] for c in got["commands"]] == ["toggle"]
        link.queue[-1]["ts"] -= 60
        assert (await link.wait(start, 0.01))["commands"] == []
        with pytest.raises(ValueError):
            link.send("explode")
        link.report({"playing": True})
        assert link.screen()["on"] is True
    asyncio.run(go())


def test_the_usb_helper_only_takes_plugged_in_tablets_that_allowed_it():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("tablet_usb", Path(__file__).parents[1] / "node" / "tablet_usb.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    out = ("List of devices attached\nR52X device usb:1 model:SM_X200\nR53Y unauthorized usb:2\n"
           "10.0.0.9:5555 device model:TV\nemulator-5554 device\nR54Z offline\n")
    assert m.plugged(out) == (["R52X"], ["R53Y"])
    assert m.plugged("") == ([], [])

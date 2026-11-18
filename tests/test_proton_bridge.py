"""Offline tests for core/proton_bridge.py — no network, no credentials."""
from __future__ import annotations

import ssl

import pytest

from config import cfg
from core import proton_bridge as pb


def test_search_criteria():
    assert pb.build_search() == "ALL"
    assert pb.build_search(from_addr="sponsor@x.com", unseen_only=True) == 'FROM "sponsor@x.com" UNSEEN'
    assert pb.build_search(subject='a "b"', since="2026-11-01") == 'SUBJECT "a b" SINCE 01-Nov-2026'
    with pytest.raises(ValueError):
        pb.build_search(since="yesterday")


def test_tls_unverified_only_for_bridge_host(monkeypatch):
    monkeypatch.setattr(cfg, "proton_bridge_host", "192.168.1.111")
    assert pb.tls_context("192.168.1.111").verify_mode == ssl.CERT_NONE
    other = pb.tls_context("smtp.example.com")
    assert other.verify_mode == ssl.CERT_REQUIRED and other.check_hostname


def test_smtp_falls_back_to_bridge(monkeypatch):
    for k, v in {"proton_bridge_host": "192.168.1.111", "smtp_host": "127.0.0.1", "smtp_port": "",
                 "proton_smtp_port": "1025", "smtp_username": "", "smtp_password": "",
                 "proton_imap_user": "me@proton.me", "proton_imap_pass": "x"}.items():
        monkeypatch.setattr(cfg, k, v)
    assert pb.smtp_settings() == ("192.168.1.111", 1025, "me@proton.me", "x")
    monkeypatch.setattr(cfg, "smtp_host", "mail.example.com")
    assert pb.smtp_settings()[0] == "mail.example.com"


def test_read_without_credentials_is_clear_error(monkeypatch):
    monkeypatch.setattr(cfg, "proton_imap_user", "")
    monkeypatch.setattr(cfg, "proton_imap_pass", "")
    with pytest.raises(pb.BridgeError, match="bridge password"):
        pb.list_messages()


RAW = (
    b"From: =?utf-8?q?Sp=C3=B6nsor?= <deals@brand.com>\r\n"
    b"To: me@proton.me\r\nSubject: Collab offer\r\nDate: Tue, 17 Nov 2026 10:00:00 +0100\r\n"
    b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XX\r\n\r\n"
    b"--XX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Hello&amp;welcome</p><br>Rate?\r\n"
    b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"brief.pdf\"\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--XX--\r\n"
)


def test_parse_message():
    m = pb.parse_message(RAW)
    assert m["from"] == "Spönsor <deals@brand.com>"
    assert m["subject"] == "Collab offer"
    assert "Hello&welcome" in m["body"] and "Rate?" in m["body"]
    assert m["attachments"] == [{"name": "brief.pdf", "type": "application/pdf", "size": 5}]


def test_writes_are_narrow():
    """No plain EXPUNGE / mailbox delete anywhere; the only UID EXPUNGE lives in
    _expunge_uids; no Trash emptying."""
    src = open(pb.__file__, encoding="utf-8").read()
    for bad in (".expunge(", ".delete(", ".deleteacl(", "EMPTY", '"EXPUNGE")'):
        assert bad not in src, bad
    assert src.count('m.uid("EXPUNGE"') == 1
    body = src[src.index("def _expunge_uids"):src.index("def _draft_raw")]
    assert 'm.uid("EXPUNGE"' in body


# ── Fake bridge for the write paths ──────────────────────────────────────────

import email as _email
import re as _re

FOLDERS = [("INBOX", "\\HasNoChildren"), ("Drafts", "\\HasNoChildren \\Drafts"),
           ("Archive", "\\HasNoChildren \\Archive"), ("Trash", "\\HasNoChildren \\Trash"),
           ("All Mail", "\\HasNoChildren \\All"), ("Labels/Sponsors", "\\HasNoChildren")]

ORIG = (
    b"From: Brand Team <deals@brand.com>\r\nTo: me@proton.me, other@brand.com\r\n"
    b"Cc: boss@brand.com\r\nSubject: Collab offer\r\nDate: Tue, 17 Nov 2026 10:00:00 +0100\r\n"
    b"Message-ID: <orig-1@brand.com>\r\nReferences: <first@brand.com>\r\n"
    b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XX\r\n\r\n"
    b"--XX\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nWhat is your rate?\r\n"
    b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"brief.pdf\"\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--XX--\r\n"
)


class FakeIMAP:
    def __init__(self, caps=("IMAP4REV1", "UIDPLUS", "MOVE")):
        self.caps = caps
        self.boxes = {n: {} for n, _ in FOLDERS}
        self.next_uid = {n: 1 for n, _ in FOLDERS}
        self.sel = None
        self.readonly = True
        self.log: list[tuple] = []
        self.add("INBOX", ORIG, {"\\Seen"})

    def add(self, box, raw, flags=()):
        uid = self.next_uid[box]
        self.next_uid[box] += 1
        self.boxes[box][uid] = [raw, set(flags)]
        return uid

    # imaplib surface
    def list(self):
        return "OK", [f'({f}) "/" "{n}"'.encode() for n, f in FOLDERS]

    def capability(self):
        return "OK", [" ".join(self.caps).encode()]

    def select(self, mailbox, readonly=False):
        name = mailbox.strip('"')
        if name not in self.boxes:
            return "NO", [b"no such folder"]
        self.sel, self.readonly = name, readonly
        self.log.append(("SELECT", name, readonly))
        return "OK", [str(len(self.boxes[name])).encode()]

    def append(self, mailbox, flags, date, raw):
        name = mailbox.strip('"')
        uid = self.add(name, raw, flags.strip("()").split())
        self.log.append(("APPEND", name, flags))
        return "OK", [f"[APPENDUID 1 {uid}] done".encode()]

    def logout(self):
        return "BYE", []

    def uid(self, cmd, *args):
        box = self.boxes[self.sel]
        self.log.append((cmd, self.sel, args))
        if cmd == "SEARCH":
            crit = args[-1]
            mid = _re.search(r'HEADER Message-ID "([^"]+)"', crit)
            if mid:
                hits = [u for u, (raw, _) in box.items()
                        if (_email.message_from_bytes(raw).get("Message-ID") or "").strip() == mid.group(1)]
            else:
                hits = list(box)
            return "OK", [" ".join(map(str, hits)).encode()]
        uids = [int(u) for u in args[0].split(",")]
        if cmd == "FETCH":
            out = []
            for u in uids:
                if u in box:
                    raw, fl = box[u]
                    out.append((f"1 (UID {u} FLAGS ({' '.join(sorted(fl))}) BODY[] {{{len(raw)}}}".encode(), raw))
                    out.append(b")")
            return "OK", out
        if self.readonly and cmd != "COPY":  # COPY leaves the source alone
            raise AssertionError(f"{cmd} on a read-only (EXAMINE) folder")
        if cmd == "STORE":
            op, flags = args[1], args[2].strip("()").split()
            for u in uids:
                if u in box:
                    (box[u][1].update if op.startswith("+") else box[u][1].difference_update)(flags)
            return "OK", []
        if cmd == "EXPUNGE":
            for u in uids:
                if u in box and "\\Deleted" in box[u][1]:
                    del box[u]
            return "OK", []
        if cmd in ("MOVE", "COPY"):
            dest = args[1].strip('"')
            for u in uids:
                if u in box:
                    self.add(dest, box[u][0], box[u][1])
                    if cmd == "MOVE":
                        del box[u]
            return "OK", []
        raise AssertionError(cmd)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeIMAP()
    monkeypatch.setattr(pb, "_connect", lambda: f)
    monkeypatch.setattr(pb, "_threads_file", lambda: str(tmp_path / "threads.json"))
    for k, v in {"proton_imap_user": "me@proton.me", "proton_imap_pass": "x", "smtp_host": "",
                 "smtp_username": "", "smtp_password": ""}.items():
        monkeypatch.setattr(cfg, k, v)
    monkeypatch.setattr(cfg, "filesystem_allowed_paths", [str(tmp_path)])
    return f


def _draft(f, uid):
    return _email.message_from_bytes(f.boxes["Drafts"][uid][0])


def test_special_folder_resolution():
    assert pb.pick_special(FOLDERS, "drafts") == "Drafts"
    assert pb.pick_special([("Brouillons", "\\Drafts")], "drafts") == "Brouillons"
    assert pb.pick_special([("Drafts", "")], "drafts") == "Drafts"


def test_subjects():
    assert pb.reply_subject("Collab offer") == "Re: Collab offer"
    assert pb.reply_subject("RE: Collab offer") == "RE: Collab offer"
    assert pb.reply_subject("Fwd: Collab") == "Re: Collab"
    assert pb.forward_subject("Collab") == "Fwd: Collab"
    assert pb.base_subject("Re: AW: Fwd: Deal") == "Deal"


def test_save_new_draft(fake):
    msg = pb.compose(to="a@b.com", subject="Hi", body="Hello", bcc="c@d.com")
    r = pb.save_draft(msg)
    assert r["folder"] == "Drafts" and r["uid"] == 1
    d = _draft(fake, 1)
    assert d["To"] == "a@b.com" and d["Bcc"] == "c@d.com" and d["Subject"] == "Hi"
    assert "\\Draft" in fake.boxes["Drafts"][1][1]
    assert not any(e[0] in ("STORE", "EXPUNGE", "MOVE") for e in fake.log)


def test_reply_draft_threads_and_quotes(fake):
    msg = pb.compose(mode="reply_all", orig_uid=1, orig_folder="INBOX", body="Thanks! My rate is X.")
    pb.save_draft(msg)
    d = _draft(fake, 1)
    assert d["Subject"] == "Re: Collab offer"
    assert d["In-Reply-To"] == "<orig-1@brand.com>"
    assert d["References"] == "<first@brand.com> <orig-1@brand.com>"
    assert d["To"] == "Brand Team <deals@brand.com>"
    assert "other@brand.com" in d["Cc"] and "boss@brand.com" in d["Cc"] and "me@proton.me" not in d["Cc"]
    text = d.get_payload(decode=True).decode()
    assert text.startswith("Thanks! My rate is X.") and "> What is your rate?" in text
    # Original was only peeked at, never marked read or changed.
    assert ("SELECT", "INBOX", True) in fake.log


def test_reply_without_quote_and_plain_reply(fake):
    d = pb.compose(mode="reply", orig_uid=1, body="Hi", quote=False)
    assert d["Cc"] is None and d.get_content().strip() == "Hi"


def test_forward_keeps_attachments(fake):
    msg = pb.compose(mode="forward", orig_uid=1, to="manager@x.com", body="FYI")
    s = pb.summarize(msg)
    assert s["subject"] == "Fwd: Collab offer"
    assert [a["name"] for a in s["attachments"]] == ["brief.pdf"]
    assert "Forwarded message" in s["body"]


def test_reply_needs_original(fake):
    with pytest.raises(pb.BridgeError, match="orig_uid"):
        pb.compose(mode="reply", body="x")


def test_attachment_paths_confined(fake, tmp_path):
    ok = tmp_path / "rate card.pdf"
    ok.write_bytes(b"%PDF-1")
    msg = pb.compose(to="a@b.com", subject="s", body="b", attachments=[str(ok)])
    assert [a["name"] for a in pb.summarize(msg)["attachments"]] == ["rate card.pdf"]
    with pytest.raises(pb.BridgeError, match="allowed"):
        pb.compose(to="a@b.com", subject="s", body="b", attachments=["/etc/passwd"])


def test_update_draft_replaces_and_keeps_thread(fake):
    pb.save_draft(pb.compose(mode="reply", orig_uid=1, body="v1"))
    r = pb.update_draft(1, body="v2", cc="x@y.com")
    assert r == {"folder": "Drafts", "old_uid": 1, "uid": 2}
    assert list(fake.boxes["Drafts"]) == [2]
    d = _draft(fake, 2)
    assert d.get_payload(decode=True).decode().strip() == "v2" and d["Cc"] == "x@y.com"
    assert d["In-Reply-To"] == "<orig-1@brand.com>" and d["Subject"] == "Re: Collab offer"
    assert len(fake.boxes["INBOX"]) == 1


def test_draft_tools_only_touch_drafts(fake):
    # UID 1 exists in INBOX but not in Drafts -> refused, INBOX untouched.
    with pytest.raises(pb.BridgeError, match="Only messages in Drafts"):
        pb.delete_draft(1)
    with pytest.raises(pb.BridgeError, match="Only messages in Drafts"):
        pb.update_draft(1, body="x")
    assert 1 in fake.boxes["INBOX"]


def test_delete_draft(fake):
    pb.save_draft(pb.compose(to="a@b.com", subject="test", body="b"))
    r = pb.delete_draft(1)
    assert r["subject"] == "test" and fake.boxes["Drafts"] == {}


def test_expunge_refuses_other_folders(fake):
    for box in ("INBOX", "Trash", "Archive", "All Mail"):
        with pytest.raises(pb.BridgeError, match="Refusing"):
            pb._expunge_uids(fake, box, [1])
    assert 1 in fake.boxes["INBOX"]


def test_expunge_needs_uidplus(fake):
    fake.caps = ("IMAP4REV1",)
    pb.save_draft(pb.compose(to="a@b.com", subject="t", body="b"))
    with pytest.raises(pb.BridgeError, match="UIDPLUS"):
        pb.delete_draft(1)
    assert 1 in fake.boxes["Drafts"]


def test_flags(fake):
    pb.set_flags("INBOX", [1], "unread")
    assert "\\Seen" not in fake.boxes["INBOX"][1][1]
    pb.set_flags("INBOX", [1], "star")
    assert "\\Flagged" in fake.boxes["INBOX"][1][1]
    with pytest.raises(pb.BridgeError):
        pb.set_flags("INBOX", [1], "delete")


def test_move_and_archive(fake):
    with pytest.raises(pb.BridgeError, match="not found"):
        pb.move_messages("INBOX", [1], "Folders/Nope")
    with pytest.raises(pb.BridgeError, match="Labels"):
        pb.move_messages("INBOX", [1], "Labels/Sponsors")
    r = pb.move_messages("INBOX", [1], "archive")
    assert r["dest"] == "Archive" and fake.boxes["INBOX"] == {} and len(fake.boxes["Archive"]) == 1


def test_labels_add_and_remove(fake):
    pb.label_messages("INBOX", [1], "Sponsors")
    assert len(fake.boxes["Labels/Sponsors"]) == 1 and 1 in fake.boxes["INBOX"]
    r = pb.label_messages("INBOX", [1], "Sponsors", remove=True)
    assert r["removed"] and fake.boxes["Labels/Sponsors"] == {} and 1 in fake.boxes["INBOX"]


def test_download_attachment(fake, tmp_path):
    saved = pb.download_attachment("INBOX", 1, str(tmp_path), index=1)
    assert saved[0]["name"] == "brief.pdf"
    assert (tmp_path / "brief.pdf").read_bytes() == b"%PDF-"
    again = pb.download_attachment("INBOX", 1, str(tmp_path), name="brief.pdf")
    assert again[0]["path"].endswith("brief (1).pdf")
    with pytest.raises(pb.BridgeError, match="allowed"):
        pb.download_attachment("INBOX", 1, "/etc")
    with pytest.raises(pb.BridgeError, match="does not exist"):
        pb.download_attachment("INBOX", 1, str(tmp_path), index=5)


def test_search_criteria_extended():
    assert pb.build_search(to_addr="me", before="2026-11-18", flagged=True, body="rate") == \
        'TO "me" BODY "rate" BEFORE 18-Nov-2026 FLAGGED'


def test_group_threads():
    rows = [
        {"uid": 1, "subject": "Collab offer", "ts": 1, "ids": ["<a@x>"]},
        {"uid": 2, "subject": "Re: Collab offer", "ts": 2, "ids": ["<b@x>", "<a@x>"]},
        {"uid": 3, "subject": "Other thing entirely", "ts": 3, "ids": ["<c@x>"]},
        {"uid": 4, "subject": "RE: Other thing entirely", "ts": 4, "ids": ["<d@x>"]},
        {"uid": 5, "subject": "Hi", "ts": 5, "ids": ["<e@x>"]},
        {"uid": 6, "subject": "Hi", "ts": 6, "ids": ["<f@x>"]},
    ]
    groups = [[r["uid"] for r in g] for g in pb.group_threads(rows)]
    assert groups == [[6], [5], [3, 4], [1, 2]]


# ── Tool layer: confirm gates and previews ───────────────────────────────────

import asyncio as _asyncio


def _tool(name):
    from mcp.server.fastmcp import FastMCP
    from tools.infrastructure import register_infrastructure_tools

    m = FastMCP("test")
    register_infrastructure_tools(m)
    t = m._tool_manager.get_tool(name)
    assert t is not None, name
    return t


def _call(name, **kw):
    t = _tool(name)
    model = t.fn_metadata.arg_model.model_fields["params"].annotation
    return _asyncio.run(t.fn(model(**kw)))


def test_send_email_reply_preview_sends_nothing(fake, monkeypatch):
    sent = []
    monkeypatch.setattr(pb, "send", lambda msg: sent.append(msg))
    out = _call("send_email", mode="reply", orig_uid=1, body="Thanks")
    assert "NOT sent" in out and "Re: Collab offer" in out and "<orig-1@brand.com>" in out
    assert sent == []
    out = _call("send_email", mode="reply", orig_uid=1, body="Thanks", confirm=True)
    assert out.startswith("✓ Email sent") and sent[0]["In-Reply-To"] == "<orig-1@brand.com>"


def test_send_email_new_needs_fields(fake):
    assert "needs to, subject and body" in _call("send_email", to="a@b.com", body="x")


def test_save_draft_tool(fake):
    out = _call("proton_save_draft", mode="reply", orig_uid=1, body="Draft reply")
    assert "Draft saved in `Drafts` — UID 1. Not sent." in out
    assert 1 in fake.boxes["Drafts"]


def test_delete_draft_tool_needs_confirm(fake):
    pb.save_draft(pb.compose(to="a@b.com", subject="test draft", body="b"))
    out = _call("proton_delete_draft", uid=1)
    assert "NOT deleted" in out and "test draft" in out and 1 in fake.boxes["Drafts"]
    out = _call("proton_delete_draft", uid=1, confirm=True)
    assert out.startswith("✓ Deleted") and fake.boxes["Drafts"] == {}


def test_move_tool_needs_confirm(fake):
    out = _call("proton_move_messages", uids=[1], dest="archive")
    assert "NOT moved" in out and "Collab offer" in out and 1 in fake.boxes["INBOX"]
    out = _call("proton_move_messages", uids=[1], dest="archive", confirm=True)
    assert out.startswith("✓ Moved") and fake.boxes["INBOX"] == {}


def test_new_tool_permissions():
    from core.agent_permissions import DANGEROUS, WRITE, is_outward

    assert {"proton_delete_draft", "proton_move_messages"} <= DANGEROUS
    assert {"proton_save_draft", "proton_update_draft", "proton_set_flags"} <= WRITE
    assert not is_outward("proton_save_draft", read_only=False)
    assert is_outward("send_email", read_only=False)
    for n in ("proton_search", "proton_list_threads", "proton_read_thread"):
        assert _tool(n).annotations.readOnlyHint is True
    assert _tool("proton_delete_draft").annotations.destructiveHint is True


# ── From alias + sending drafts as real replies ──────────────────────────────

ALIASED = ORIG.replace(b"To: me@proton.me, other@brand.com\r\n",
                       b"Delivered-To: sponsorships@thefrizzy1.com\r\n"
                       b"To: sponsorships@thefrizzy1.com, other@brand.com\r\n")


@pytest.fixture
def threads_file(monkeypatch, tmp_path):
    f = tmp_path / "threads.json"
    monkeypatch.setattr(pb, "_threads_file", lambda: str(f))
    return f


def test_explicit_from_alias(fake, threads_file):
    msg = pb.compose(to="a@b.com", subject="s", body="b", from_addr="sponsorships@thefrizzy1.com")
    assert msg["From"] == "sponsorships@thefrizzy1.com"
    with pytest.raises(pb.BridgeError, match="exactly one"):
        pb.compose(to="a@b.com", subject="s", body="b", from_addr="a@b.com, c@d.com")


def test_reply_defaults_to_received_alias(fake, threads_file):
    fake.boxes["INBOX"][1][0] = ALIASED
    msg = pb.compose(mode="reply_all", orig_uid=1, body="hi")
    assert msg["From"] == "sponsorships@thefrizzy1.com"
    assert "sponsorships@thefrizzy1.com" not in (msg["Cc"] or "")
    assert "other@brand.com" in msg["Cc"]


def test_update_draft_can_change_from(fake, threads_file):
    pb.save_draft(pb.compose(mode="reply", orig_uid=1, body="v1"))
    pb.update_draft(1, from_addr="Frizzy <sponsorships@thefrizzy1.com>")
    assert _draft(fake, 2)["From"] == "Frizzy <sponsorships@thefrizzy1.com>"


def test_forward_sets_forward_header(fake, threads_file):
    msg = pb.compose(mode="forward", orig_uid=1, to="m@x.com")
    assert msg["X-Forwarded-Message-Id"] == msg["In-Reply-To"] == "<orig-1@brand.com>"


def test_send_draft_restores_thread_and_removes_draft(fake, threads_file, monkeypatch):
    sent = []
    monkeypatch.setattr(pb, "send", lambda m: sent.append(m))
    pb.save_draft(pb.compose(mode="reply", orig_uid=1, body="Rate is X"))
    # Simulate the bridge serving the draft back without thread headers.
    raw = fake.boxes["Drafts"][1][0]
    raw = b"".join(l for l in raw.splitlines(True)
                   if not l.lower().startswith((b"in-reply-to", b"references")))
    fake.boxes["Drafts"][1][0] = raw
    msg, info = pb.prepare_draft_send(1)
    assert info["restored"] and info["threaded"] and sent == []
    r = pb.send_draft(1)
    assert sent[0]["In-Reply-To"] == "<orig-1@brand.com>"
    assert "<orig-1@brand.com>" in sent[0]["References"]
    assert r["draft_removed"] is True and fake.boxes["Drafts"] == {}


def test_send_draft_tool_needs_confirm(fake, threads_file, monkeypatch):
    sent = []
    monkeypatch.setattr(pb, "send", lambda m: sent.append(m))
    pb.save_draft(pb.compose(mode="reply", orig_uid=1, body="Rate is X"))
    out = _call("proton_send_draft", uid=1)
    assert "NOT sent" in out and "reply to <orig-1@brand.com>" in out and sent == []
    out = _call("proton_send_draft", uid=1, confirm=True)
    assert out.startswith("✓ Sent") and len(sent) == 1


def test_send_draft_is_outward_and_dangerous():
    from core.agent_permissions import DANGEROUS, is_outward
    assert "proton_send_draft" in DANGEROUS and is_outward("proton_send_draft", read_only=False)


def test_send_draft_with_explicit_original(fake, threads_file, monkeypatch):
    sent = []
    monkeypatch.setattr(pb, "send", lambda m: sent.append(m))
    pb.save_draft(pb.compose(to="deals@brand.com", subject="Re: Collab offer", body="old draft"))
    msg, info = pb.prepare_draft_send(1)
    assert not info["threaded"]
    pb.send_draft(1, orig_uid=1, orig_folder="INBOX")
    assert sent[0]["In-Reply-To"] == "<orig-1@brand.com>"


def test_create_folder(fake):
    created = []
    fake.create = lambda name: (created.append(name.strip('"')), ("OK", []))[1]
    assert pb.create_folder("Labels/New") == "Labels/New" and created == ["Labels/New"]
    with pytest.raises(pb.BridgeError, match="Folders/ or Labels/"):
        pb.create_folder("INBOX2")
    with pytest.raises(pb.BridgeError, match="already exists"):
        pb.create_folder("Labels/Sponsors")

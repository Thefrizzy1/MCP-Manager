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


def test_no_mutating_imap_calls_in_module():
    src = open(pb.__file__, encoding="utf-8").read()
    for bad in (".store(", ".expunge(", ".copy(", ".delete(", '"MOVE"', "readonly=False", "BODY[]"):
        assert bad not in src.replace("BODY.PEEK[]", ""), bad

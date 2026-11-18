"""core/proton_bridge.py — Proton Mail Bridge over plain IMAP/SMTP + STARTTLS.

The bridge runs as the ``protonmail-bridge`` container on plutus
(192.168.1.111): IMAP on host port 1143, SMTP on host port 1025. Both upgrade
with STARTTLS using the bridge's own self-signed certificate, so certificate
verification is switched off *only* when talking to the configured bridge host
(``PROTON_BRIDGE_HOST``); any other host keeps full verification.

Reading is strictly read-only: folders are opened with EXAMINE and bodies are
fetched with ``BODY.PEEK[]`` so nothing is ever marked as read. There is no
delete / move / flag code here on purpose.

All functions are blocking; tools call them through ``asyncio.to_thread``.
"""
from __future__ import annotations

import email
import imaplib
import re
import smtplib
import socket
import ssl
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from config import cfg

TIMEOUT = 15

# Bridge credentials are the *bridge* password shown in the Proton Mail Bridge
# app (Settings → account → mailbox details), not the Proton account login.
_CRED_HINT = (
    "Set PROTON_IMAP_USER (your Proton address) and PROTON_IMAP_PASS (the bridge "
    "password shown in the Proton Mail Bridge app — not your Proton login) in "
    "Plutus's .env or the dashboard 'Email / Proton Bridge' card."
)


# ── Settings ──────────────────────────────────────────────────────────────────

def bridge_host() -> str:
    return cfg.proton_bridge_host or "192.168.1.111"


def imap_port() -> int:
    return int(cfg.proton_imap_port or 1143)


def smtp_settings() -> tuple[str, int, str, str]:
    """(host, port, username, password) for sending.

    Explicit SMTP_* values win; otherwise the bridge settings are used. A
    loopback SMTP_HOST (the old default, from when the bridge ran on the Windows
    PC) can never be the bridge from inside the container, so it also falls
    back to the bridge host.
    """
    host = (cfg.smtp_host or "").strip()
    if host in ("", "127.0.0.1", "localhost", "::1"):
        host = bridge_host()
    port = int(cfg.smtp_port or cfg.proton_smtp_port or 1025)
    user = cfg.smtp_username or cfg.proton_imap_user
    password = cfg.smtp_password or cfg.proton_imap_pass
    return host, port, user, password


def has_credentials() -> bool:
    return bool(cfg.proton_imap_user and cfg.proton_imap_pass)


def tls_context(host: str) -> ssl.SSLContext:
    """Verified TLS everywhere except the bridge host's self-signed cert."""
    ctx = ssl.create_default_context()
    if host == bridge_host():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── Status probe (no credentials needed) ──────────────────────────────────────

def _read_line(host: str, port: int, send: bytes | None = None) -> str:
    with socket.create_connection((host, port), timeout=5) as s:
        s.settimeout(5)
        greeting = s.recv(1024).decode("utf-8", "replace").strip()
        if send:
            s.sendall(send)
            try:
                s.recv(1024)
            except OSError:
                pass
        return greeting


def probe() -> dict:
    """Connect to IMAP and SMTP, read the greetings, try STARTTLS, and (when
    credentials exist) try an IMAP login. Never sends mail."""
    host = bridge_host()
    out: dict = {"host": host, "imap": {}, "smtp": {}, "login": None}

    iport = imap_port()
    try:
        m = imaplib.IMAP4(host, iport, timeout=TIMEOUT)
        greeting = m.welcome.decode("utf-8", "replace")
        out["imap"] = {"port": iport, "ok": True, "greeting": greeting}
        ver = re.search(r"Proton Mail Bridge\s+([\d.]+)", greeting)
        out["version"] = ver.group(1) if ver else None
        try:
            m.starttls(ssl_context=tls_context(host))
            out["imap"]["starttls"] = True
            if has_credentials():
                try:
                    m.login(cfg.proton_imap_user, cfg.proton_imap_pass)
                    out["login"] = "ok"
                except imaplib.IMAP4.error as e:
                    out["login"] = f"failed: {_err(e)}"
        except Exception as e:  # noqa: BLE001
            out["imap"]["starttls"] = f"failed: {_err(e)}"
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        out["imap"] = {"port": iport, "ok": False, "error": _err(e)}

    shost, sport, _, _ = smtp_settings()
    try:
        with smtplib.SMTP(shost, sport, timeout=TIMEOUT) as s:
            code, msg = s.ehlo()
            text = msg.decode("utf-8", "replace")
            out["smtp"] = {"host": shost, "port": sport, "ok": code == 250,
                           "starttls": "STARTTLS" in text.upper()}
    except Exception as e:  # noqa: BLE001
        out["smtp"] = {"host": shost, "port": sport, "ok": False, "error": _err(e)}
    return out


def _err(e: Exception) -> str:
    if isinstance(e, imaplib.IMAP4.error) and e.args:
        a = e.args[0]
        return a.decode("utf-8", "replace") if isinstance(a, bytes) else str(a)
    return f"{type(e).__name__}: {e}"


# ── IMAP reading (read-only) ──────────────────────────────────────────────────

class BridgeError(RuntimeError):
    pass


def _connect() -> imaplib.IMAP4:
    if not has_credentials():
        raise BridgeError("Proton Bridge credentials not configured. " + _CRED_HINT)
    host = bridge_host()
    try:
        m = imaplib.IMAP4(host, imap_port(), timeout=TIMEOUT)
    except OSError as e:
        raise BridgeError(f"Cannot reach Proton Bridge IMAP at {host}:{imap_port()} — {e}") from e
    m.starttls(ssl_context=tls_context(host))
    try:
        m.login(cfg.proton_imap_user, cfg.proton_imap_pass)
    except imaplib.IMAP4.error as e:
        raise BridgeError(f"Bridge login failed ({_err(e)}). " + _CRED_HINT) from e
    return m


def _quote(mailbox: str) -> str:
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _examine(m: imaplib.IMAP4, folder: str) -> int:
    typ, data = m.select(_quote(folder), readonly=True)  # readonly=True -> EXAMINE
    if typ != "OK":
        raise BridgeError(f"Folder not found: {folder}")
    try:
        return int(data[0])
    except (TypeError, ValueError, IndexError):
        return 0


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)')


def list_folders() -> list[dict]:
    m = _connect()
    try:
        typ, data = m.list()
        folders = []
        for raw in data or []:
            if not isinstance(raw, bytes):
                continue
            mt = _LIST_RE.match(raw)
            if not mt:
                continue
            name = mt.group("name").decode("utf-8", "replace").strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            flags = mt.group("flags").decode()
            entry = {"name": name, "flags": flags}
            if "\\Noselect" not in flags:
                try:
                    typ, st = m.status(_quote(name), "(MESSAGES UNSEEN)")
                    if typ == "OK" and st and st[0]:
                        s = st[0].decode("utf-8", "replace")
                        mm = re.search(r"MESSAGES (\d+)", s)
                        mu = re.search(r"UNSEEN (\d+)", s)
                        entry["messages"] = int(mm.group(1)) if mm else None
                        entry["unseen"] = int(mu.group(1)) if mu else None
                except Exception:  # noqa: BLE001
                    pass
            folders.append(entry)
        return folders
    finally:
        _logout(m)


def _imap_str(v: str) -> str:
    return '"' + v.replace("\\", "").replace('"', "") + '"'


def build_search(from_addr: str = "", subject: str = "", text: str = "",
                 since: str = "", unseen_only: bool = False) -> str:
    """IMAP SEARCH criteria. ``since`` is YYYY-MM-DD."""
    parts: list[str] = []
    if from_addr:
        parts += ["FROM", _imap_str(from_addr)]
    if subject:
        parts += ["SUBJECT", _imap_str(subject)]
    if text:
        parts += ["TEXT", _imap_str(text)]
    if since:
        from datetime import date
        d = date.fromisoformat(since)
        parts += ["SINCE", d.strftime("%d-%b-%Y")]
    if unseen_only:
        parts.append("UNSEEN")
    return " ".join(parts) or "ALL"


def _decode(v: str | None) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:  # noqa: BLE001
        return v


_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
_UID_RE = re.compile(rb"UID (\d+)")


def list_messages(folder: str = "INBOX", limit: int = 10, **criteria) -> dict:
    m = _connect()
    try:
        total = _examine(m, folder)
        typ, data = m.uid("SEARCH", None, build_search(**criteria))
        uids = (data[0] or b"").split() if typ == "OK" and data else []
        matched = len(uids)
        # Highest UID = newest arrival in this folder.
        uids = sorted(uids, key=int, reverse=True)[:limit]
        rows: list[dict] = []
        if uids:
            typ, data = m.uid(
                "FETCH", b",".join(uids).decode(),
                "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
            )
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                meta, hdr = item[0], item[1]
                hm = email.message_from_bytes(hdr)
                uid = _UID_RE.search(meta)
                fl = _FLAGS_RE.search(meta)
                rows.append({
                    "uid": int(uid.group(1)) if uid else None,
                    "from": _decode(hm.get("From")),
                    "subject": _decode(hm.get("Subject")),
                    "date": _decode(hm.get("Date")),
                    "flags": fl.group(1).decode() if fl else "",
                })
            rows.sort(key=lambda r: r["uid"] or 0, reverse=True)
        return {"folder": folder, "total": total, "matched": matched, "messages": rows}
    finally:
        _logout(m)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    import html as _h
    text = _h.unescape(text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def parse_message(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    plain: list[str] = []
    html: list[str] = []
    attachments: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        ctype = part.get_content_type()
        if filename or disp == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append({"name": _decode(filename) or "(unnamed)",
                                "type": ctype, "size": len(payload)})
        elif ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            html.append(_part_text(part))
    body = "\n".join(plain).strip() or _html_to_text("\n".join(html))
    date = msg.get("Date")
    try:
        iso = parsedate_to_datetime(date).isoformat() if date else ""
    except Exception:  # noqa: BLE001
        iso = ""
    return {
        "from": _decode(msg.get("From")),
        "to": _decode(msg.get("To")),
        "cc": _decode(msg.get("Cc")),
        "reply_to": _decode(msg.get("Reply-To")),
        "subject": _decode(msg.get("Subject")),
        "date": _decode(date),
        "date_iso": iso,
        "message_id": msg.get("Message-ID", ""),
        "body": body,
        "attachments": attachments,
    }


def read_message(uid: int, folder: str = "INBOX") -> dict:
    m = _connect()
    try:
        _examine(m, folder)
        typ, data = m.uid("FETCH", str(int(uid)), "(FLAGS BODY.PEEK[])")
        raw = None
        flags = ""
        for item in data or []:
            if isinstance(item, tuple):
                raw = item[1]
                fl = _FLAGS_RE.search(item[0])
                flags = fl.group(1).decode() if fl else ""
        if raw is None:
            raise BridgeError(f"No message with UID {uid} in {folder}.")
        out = parse_message(raw)
        out.update({"uid": int(uid), "folder": folder, "flags": flags})
        return out
    finally:
        _logout(m)


def _logout(m: imaplib.IMAP4) -> None:
    try:
        m.logout()
    except Exception:  # noqa: BLE001
        pass


# ── SMTP sending ──────────────────────────────────────────────────────────────

def send(msg: Message) -> None:
    host, port, user, password = smtp_settings()
    if not (user and password):
        raise BridgeError("SMTP credentials not configured. " + _CRED_HINT)
    with smtplib.SMTP(host, port, timeout=TIMEOUT) as server:
        server.ehlo()
        if server.has_extn("starttls"):
            server.starttls(context=tls_context(host))
            server.ehlo()
        server.login(user, password)
        server.send_message(msg)

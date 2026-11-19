"""core/proton_bridge.py — Proton Mail Bridge over plain IMAP/SMTP + STARTTLS.

The bridge runs as the ``protonmail-bridge`` container on plutus
(192.168.1.111): IMAP on host port 1143, SMTP on host port 1025. Both upgrade
with STARTTLS using the bridge's own self-signed certificate, so certificate
verification is switched off *only* when talking to the configured bridge host
(``PROTON_BRIDGE_HOST``); any other host keeps full verification.

Reading is read-only: folders are opened with EXAMINE and bodies are fetched
with ``BODY.PEEK[]`` so nothing is marked as read by looking at it.

Writing is narrow and explicit:
- drafts are APPENDed to the Drafts folder with the Draft flag; update/delete only
  ever touch the Drafts folder;
- flags (read / unread / star) via UID STORE;
- move / archive via UID MOVE, labels via UID COPY;
- the only expunge is ``_expunge_uids``: UID EXPUNGE of named UIDs, and only in
  Drafts or a ``Labels/…`` folder (removing a label). Nothing here ever
  expunges Trash, Spam, INBOX or a real folder, so Trash is never emptied.

All functions are blocking; tools call them through ``asyncio.to_thread``.
"""
from __future__ import annotations

import email
import imaplib
import mimetypes
import os
import re
import smtplib
import socket
import ssl
import time
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import formatdate, getaddresses, make_msgid, parsedate_to_datetime

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


def parse_list(data) -> list[tuple[str, str]]:
    """IMAP LIST response -> [(name, flags)]."""
    out: list[tuple[str, str]] = []
    for raw in data or []:
        if not isinstance(raw, bytes):
            continue
        mt = _LIST_RE.match(raw)
        if not mt:
            continue
        name = mt.group("name").decode("utf-8", "replace").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out.append((name, mt.group("flags").decode()))
    return out


def list_folders() -> list[dict]:
    m = _connect()
    try:
        typ, data = m.list()
        folders = []
        for name, flags in parse_list(data):
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
                 since: str = "", unseen_only: bool = False, *, to_addr: str = "",
                 body: str = "", before: str = "", flagged: bool = False) -> str:
    """IMAP SEARCH criteria. ``since`` / ``before`` are YYYY-MM-DD
    (since = on or after, before = strictly before)."""
    from datetime import date
    parts: list[str] = []
    if from_addr:
        parts += ["FROM", _imap_str(from_addr)]
    if to_addr:
        parts += ["TO", _imap_str(to_addr)]
    if subject:
        parts += ["SUBJECT", _imap_str(subject)]
    if text:
        parts += ["TEXT", _imap_str(text)]
    if body:
        parts += ["BODY", _imap_str(body)]
    if since:
        parts += ["SINCE", date.fromisoformat(since).strftime("%d-%b-%Y")]
    if before:
        parts += ["BEFORE", date.fromisoformat(before).strftime("%d-%b-%Y")]
    if unseen_only:
        parts.append("UNSEEN")
    if flagged:
        parts.append("FLAGGED")
    return " ".join(parts) or "ALL"


_FOLD_RE = re.compile(r"\r?\n(?=[ \t])")


def _decode(v: str | None) -> str:
    """Decode an RFC 2047 header value and unfold it.

    Long headers (and attachment filenames) arrive folded: CRLF + whitespace.
    Unfolding drops the CRLF and keeps the whitespace (RFC 5322 §2.2.3); any
    stray CR/LF left after decoding becomes a space, so names never carry
    line breaks.
    """
    if not v:
        return ""
    v = _FOLD_RE.sub("", str(v))
    try:
        out = str(make_header(decode_header(v)))
    except Exception:  # noqa: BLE001
        out = v
    return re.sub(r"[ \t]*[\r\n]+[ \t]*", " ", out).strip()


_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
_UID_RE = re.compile(rb"UID (\d+)")


def list_messages(folder: str = "INBOX", limit: int = 10, **criteria) -> dict:
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
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
        "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
        "references": " ".join((msg.get("References") or "").split()),
        "body": body,
        "attachments": attachments,
    }


def read_message(uid: int, folder: str = "INBOX") -> dict:
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
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


# ── Folder resolution ─────────────────────────────────────────────────────────

# Special-use flag (RFC 6154) and the bridge's default English name, per role.
SPECIAL_FOLDERS: dict[str, tuple[str, str]] = {
    "drafts": ("\\Drafts", "Drafts"),
    "archive": ("\\Archive", "Archive"),
    "trash": ("\\Trash", "Trash"),
    "spam": ("\\Junk", "Spam"),
    "sent": ("\\Sent", "Sent"),
    "all": ("\\All", "All Mail"),
    "starred": ("\\Flagged", "Starred"),
}


def pick_special(folders: list[tuple[str, str]], role: str) -> str:
    flag, default = SPECIAL_FOLDERS[role]
    for name, flags in folders:
        if flag.lower() in flags.lower().split():
            return name
    for name, _ in folders:
        if name.lower() == default.lower():
            return name
    return default


def special_folder(m: imaplib.IMAP4, role: str) -> str:
    typ, data = m.list()
    return pick_special(parse_list(data), role)


def resolve_folder(m: imaplib.IMAP4, folder: str) -> str:
    """'' or 'all' -> All Mail; 'drafts' / 'archive' / 'trash' / 'spam' /
    'sent' / 'starred' (any case) -> the real folder name; anything else as is."""
    key = (folder or "all").strip().lower()
    if key == "all mail":
        key = "all"
    if key in SPECIAL_FOLDERS:
        return special_folder(m, key)
    return folder


def _role_of(m: imaplib.IMAP4, folder: str) -> str | None:
    typ, data = m.list()
    folders = parse_list(data)
    for role in SPECIAL_FOLDERS:
        if pick_special(folders, role) == folder:
            return role
    return None


def _folder_exists(m: imaplib.IMAP4, folder: str) -> bool:
    typ, data = m.list()
    return any(n == folder for n, _ in parse_list(data))


def _caps(m: imaplib.IMAP4) -> set[str]:
    try:
        typ, data = m.capability()
        if typ == "OK" and data and data[0]:
            return {c.upper() for c in data[0].decode("utf-8", "replace").split()}
    except Exception:  # noqa: BLE001
        pass
    return {str(c).upper() for c in getattr(m, "capabilities", ())}


def _select_rw(m: imaplib.IMAP4, folder: str) -> int:
    typ, data = m.select(_quote(folder), readonly=False)
    if typ != "OK":
        raise BridgeError(f"Folder not found: {folder}")
    try:
        return int(data[0])
    except (TypeError, ValueError, IndexError):
        return 0


def uid_set(uids) -> str:
    out = []
    for u in uids:
        n = int(u)
        if n < 1:
            raise BridgeError(f"Bad UID: {u}")
        out.append(str(n))
    if not out:
        raise BridgeError("No UIDs given.")
    return ",".join(out)


# ── Paths (attachments in / out) ──────────────────────────────────────────────

def allowed_roots() -> list[str]:
    """Same confinement as the fs_* tools: FILESYSTEM_ALLOWED_PATHS + library."""
    roots = list(cfg.filesystem_allowed_paths)
    try:
        from core.library import library_roots
        roots += library_roots()
    except Exception:  # noqa: BLE001
        pass
    return roots


def check_path(path: str) -> str:
    from core.path_guard import is_within_any
    if not path or not is_within_any(path, allowed_roots()):
        allowed = ", ".join(allowed_roots()) or "(none configured)"
        raise BridgeError(f"Path '{path}' is not in allowed directories: {allowed}")
    return os.path.realpath(path)


def _safe_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(". ")
    return name or "attachment"


# ── Composing ─────────────────────────────────────────────────────────────────

_PREFIX_RE = re.compile(r"^\s*((re|fwd?|aw|wg|sv|antw)\s*(\[\d+\])?\s*:\s*)+", re.I)


def base_subject(subject: str) -> str:
    return _PREFIX_RE.sub("", subject or "").strip()


def reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if re.match(r"^\s*re\s*:", s, re.I) else f"Re: {base_subject(s) or s}".strip()


def forward_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if re.match(r"^\s*fwd?\s*:", s, re.I) else f"Fwd: {s}".strip()


def _addr_list(*values: str) -> list[tuple[str, str]]:
    return [(n, a) for n, a in getaddresses([v for v in values if v]) if a]


def _fmt_addrs(pairs: list[tuple[str, str]]) -> str:
    from email.utils import formataddr
    return ", ".join(formataddr((n, a)) if n else a for n, a in pairs)


def _attachment_parts(raw: bytes) -> list[dict]:
    """[{name, type, data}] for every attachment in a raw message, in order."""
    msg = email.message_from_bytes(raw)
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if filename or disp == "attachment":
            out.append({"name": _decode(filename) or "attachment",
                        "type": part.get_content_type(),
                        "data": part.get_payload(decode=True) or b""})
    return out


def _html_body(raw: bytes) -> str:
    msg = email.message_from_bytes(raw)
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        if part.get_content_type() == "text/html" and disp != "attachment" and not part.get_filename():
            return _part_text(part)
    return ""


def received_as(o: Message, self_addr: str = "") -> str:
    """The address of yours the original was delivered to (an alias such as
    sponsorships@… rather than the login address), from Delivered-To /
    X-Original-To, cross-checked against To/Cc. '' if unknown."""
    rcpts = {a.lower(): a for _, a in _addr_list(o.get("To", ""), o.get("Cc", ""))}
    for h in ("Delivered-To", "X-Original-To"):
        for v in o.get_all(h) or []:
            for _, a in _addr_list(v):
                if a.lower() in rcpts:
                    return rcpts[a.lower()]
    me = (self_addr or "").lower()
    return rcpts.get(me, "") if me else ""


def from_header(addr: str) -> str:
    """Validate one From address ('Name <a@b>' or 'a@b')."""
    pairs = _addr_list(addr)
    if len(pairs) != 1 or "@" not in pairs[0][1]:
        raise BridgeError(f"from_addr must be exactly one email address, got: {addr!r}")
    return _fmt_addrs(pairs)


def thread_context(orig_raw: bytes, *, mode: str, self_addr: str = "",
                   quote: bool = True) -> dict:
    """Headers + quoted text for a reply / reply_all / forward of ``orig_raw``.

    Returns {to, cc, subject, in_reply_to, references, quoted, attachments,
    from}; ``from`` is the address the original was received at ('' if unknown).
    """
    o = email.message_from_bytes(orig_raw)
    p = parse_message(orig_raw)
    msgid = (o.get("Message-ID") or "").strip()
    refs = " ".join((o.get("References") or "").split())
    if msgid and msgid not in refs.split():
        refs = f"{refs} {msgid}".strip()
    out = {"to": "", "cc": "", "subject": "", "in_reply_to": msgid,
           "references": refs, "quoted": "", "attachments": [],
           "from": received_as(o, self_addr)}
    me = (self_addr or "").lower()
    mine = {x for x in (me, out["from"].lower()) if x}
    if mode in ("reply", "reply_all"):
        target = _addr_list(o.get("Reply-To", "")) or _addr_list(o.get("From", ""))
        # Replying to your own sent mail goes back to its original recipients.
        if target and all(a.lower() in mine for _, a in target):
            target = _addr_list(o.get("To", "")) or target
        out["to"] = _fmt_addrs(target)
        if mode == "reply_all":
            seen = {a.lower() for _, a in target} | mine
            cc = []
            for n, a in _addr_list(o.get("To", ""), o.get("Cc", "")):
                if a.lower() not in seen:
                    seen.add(a.lower())
                    cc.append((n, a))
            out["cc"] = _fmt_addrs(cc)
        out["subject"] = reply_subject(p["subject"])
        if quote:
            body = "\n".join("> " + ln if ln else ">" for ln in p["body"].splitlines())
            out["quoted"] = f"On {p['date']}, {p['from']} wrote:\n{body}"
    elif mode == "forward":
        out["subject"] = forward_subject(p["subject"])
        head = ["---------- Forwarded message ----------",
                f"From: {p['from']}", f"Date: {p['date']}", f"Subject: {p['subject']}",
                f"To: {p['to']}"]
        if p["cc"]:
            head.append(f"Cc: {p['cc']}")
        out["quoted"] = "\n".join(head) + "\n\n" + p["body"]
        out["attachments"] = _attachment_parts(orig_raw)
    else:
        raise BridgeError(f"Unknown mode: {mode}")
    return out


def build_email(*, from_addr: str, to: str = "", cc: str = "", bcc: str = "",
                subject: str = "", body: str = "", html: str = "",
                attachment_paths=(), extra_attachments=(),
                in_reply_to: str = "", references: str = "",
                extra_headers: dict | None = None) -> EmailMessage:
    """One RFC 5322 message. ``attachment_paths`` are read from disk (confined to
    allowed directories); ``extra_attachments`` are {name, type, data} dicts."""
    msg = EmailMessage()
    msg["From"] = from_addr
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1].strip("> ") if "@" in from_addr else None)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    for k, v in (extra_headers or {}).items():
        if v:
            msg[k] = v
    msg.set_content(body or "")
    if html:
        msg.add_alternative(html, subtype="html")
    for path in attachment_paths or ():
        real = check_path(path)
        if not os.path.isfile(real):
            raise BridgeError(f"Attachment not found: {path}")
        with open(real, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype,
                           filename=os.path.basename(real))
    for a in extra_attachments or ():
        ctype = a.get("type") or "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(a["data"], maintype=maintype, subtype=subtype or "octet-stream",
                           filename=_safe_filename(a.get("name", "")))
    return msg


def _fetch_raw(m: imaplib.IMAP4, uid: int) -> tuple[bytes | None, str]:
    typ, data = m.uid("FETCH", str(int(uid)), "(FLAGS BODY.PEEK[])")
    for item in data or []:
        if isinstance(item, tuple):
            fl = _FLAGS_RE.search(item[0])
            return item[1], (fl.group(1).decode() if fl else "")
    return None, ""


def compose(*, mode: str = "new", to: str = "", cc: str = "", bcc: str = "",
            subject: str = "", body: str = "", html: str = "", attachments=(),
            orig_uid: int = 0, orig_folder: str = "INBOX", quote: bool = True,
            include_original_attachments: bool = True, from_addr: str = "") -> EmailMessage:
    """Build a new / reply / reply_all / forward message (not sent, not saved).

    For replies and forwards the original is read with BODY.PEEK (not marked
    read). Explicit to/cc/subject win over the derived ones; derived cc from
    reply_all is merged with any explicit cc.

    From: explicit ``from_addr`` (e.g. an alias like sponsorships@…) wins; for
    replies/forwards it defaults to the address the original was received at;
    otherwise the account login address.
    """
    account = smtp_settings()[2] or cfg.proton_imap_user
    mode = (mode or "new").lower()
    ctx = None
    if mode != "new":
        if not orig_uid:
            raise BridgeError(f"mode={mode} needs the original message (orig_uid + orig_folder).")
        m = _connect()
        try:
            folder = resolve_folder(m, orig_folder)
            _examine(m, folder)
            raw, _ = _fetch_raw(m, orig_uid)
        finally:
            _logout(m)
        if raw is None:
            raise BridgeError(f"No message with UID {orig_uid} in {orig_folder}.")
        ctx = thread_context(raw, mode=mode, self_addr=account, quote=quote)
    from_addr = from_header(from_addr) if from_addr else ((ctx or {}).get("from") or account)
    if ctx:
        to = to or ctx["to"]
        if ctx["cc"]:
            cc = ", ".join(x for x in (cc, ctx["cc"]) if x)
        subject = subject or ctx["subject"]
        if ctx["quoted"]:
            body = f"{body.rstrip()}\n\n{ctx['quoted']}" if body.strip() else ctx["quoted"]
    return build_email(
        from_addr=from_addr, to=to, cc=cc, bcc=bcc, subject=subject, body=body, html=html,
        attachment_paths=attachments,
        extra_attachments=(ctx["attachments"] if ctx and include_original_attachments else ()),
        in_reply_to=ctx["in_reply_to"] if ctx else "",
        references=ctx["references"] if ctx else "",
        # The bridge's SMTP marks a send as a forward (not a reply) when
        # In-Reply-To equals X-Forwarded-Message-Id.
        extra_headers={"X-Forwarded-Message-Id": ctx["in_reply_to"]} if ctx and mode == "forward" else None,
    )


def summarize(msg: Message) -> dict:
    """Header / attachment summary of a composed message (for previews)."""
    atts = []
    body = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_filename():
            atts.append({"name": _decode(part.get_filename()), "type": part.get_content_type(),
                         "size": len(part.get_payload(decode=True) or b"")})
        elif part.get_content_type() == "text/plain" and not body:
            body = _part_text(part)
    return {k: str(msg.get(h, "")) for k, h in (
        ("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("subject", "Subject"),
        ("in_reply_to", "In-Reply-To"), ("references", "References"))} | {
        "body": body, "attachments": atts,
        "html": any(p.get_content_type() == "text/html" for p in msg.walk())}


# ── Drafts ────────────────────────────────────────────────────────────────────

_APPENDUID_RE = re.compile(rb"APPENDUID \d+ (\d+)")


def _append_draft(m: imaplib.IMAP4, drafts: str, msg: Message) -> int | None:
    raw = msg.as_bytes(policy=policy.SMTP)
    typ, data = m.append(_quote(drafts), r"(\Draft \Seen)",
                         imaplib.Time2Internaldate(time.time()), raw)
    if typ != "OK":
        raise BridgeError(f"Saving the draft failed: {data!r}")
    for d in data or []:
        if isinstance(d, bytes):
            mt = _APPENDUID_RE.search(d)
            if mt:
                return int(mt.group(1))
    # No UIDPLUS answer: find it by Message-ID.
    msgid = str(msg.get("Message-ID", "")).strip()
    if msgid:
        _examine(m, drafts)
        typ, data = m.uid("SEARCH", None, "HEADER Message-ID " + _imap_str(msgid))
        uids = (data[0] or b"").split() if typ == "OK" and data else []
        if uids:
            return int(max(uids, key=int))
    return None


# The bridge creates IMAP drafts without a parent message, and the draft it
# serves back may lose In-Reply-To / References. So the thread headers of every
# saved draft are also kept here, and restored when the draft is sent via SMTP —
# the bridge's SMTP path links a send to its parent from those headers.

def _threads_file() -> str:
    from core.library import default_root
    return os.path.join(str(default_root()), "data", "proton_draft_threads.json")


def _thread_keys(msg: Message) -> list[str]:
    keys = []
    mid = str(msg.get("Message-ID") or "").strip()
    if mid:
        keys.append("id:" + mid)
    subj = base_subject(_decode(msg.get("Subject"))).lower()
    to = ",".join(sorted(a.lower() for _, a in _addr_list(_decode(msg.get("To")))))
    if subj and to:
        keys.append(f"st:{subj}|{to}")
    return keys


def _load_threads() -> dict:
    import json
    try:
        with open(_threads_file(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def remember_thread(msg: Message) -> None:
    irt = str(msg.get("In-Reply-To") or "").strip()
    if not irt:
        return
    import json
    data = _load_threads()
    entry = {"in_reply_to": irt,
             "references": " ".join(str(msg.get("References") or "").split()),
             "forward_id": str(msg.get("X-Forwarded-Message-Id") or "").strip(),
             "ts": time.time()}
    for k in _thread_keys(msg):
        data[k] = entry
    if len(data) > 400:
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("ts", 0))[-400:])
    try:
        path = _threads_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def lookup_thread(msg: Message) -> dict | None:
    data = _load_threads()
    for k in _thread_keys(msg):
        if k in data:
            return data[k]
    return None


def save_draft(msg: Message) -> dict:
    remember_thread(msg)
    m = _connect()
    try:
        drafts = special_folder(m, "drafts")
        uid = _append_draft(m, drafts, msg)
        return {"folder": drafts, "uid": uid, "message_id": str(msg.get("Message-ID", ""))}
    finally:
        _logout(m)


def _expunge_uids(m: imaplib.IMAP4, folder: str, uids) -> None:
    """The ONLY expunge in this module. UID EXPUNGE of exactly these UIDs, and
    only in Drafts (deleting a draft) or Labels/… (removing a label). Refuses
    every other folder — INBOX, Trash, Spam, Archive, Folders/… — so it can
    never empty Trash or delete real mail."""
    role = _role_of(m, folder)
    if role != "drafts" and not folder.startswith("Labels/"):
        raise BridgeError(f"Refusing to expunge in '{folder}': only Drafts or Labels/… allowed.")
    if "UIDPLUS" not in _caps(m):
        raise BridgeError("Bridge lacks UIDPLUS, so a targeted delete is not possible; nothing changed.")
    s = uid_set(uids)
    _select_rw(m, folder)
    typ, data = m.uid("STORE", s, "+FLAGS.SILENT", r"(\Deleted)")
    if typ != "OK":
        raise BridgeError(f"Flagging for delete failed: {data!r}")
    typ, data = m.uid("EXPUNGE", s)
    if typ != "OK":
        raise BridgeError(f"Expunge failed: {data!r}")


def _draft_raw(m: imaplib.IMAP4, uid: int) -> tuple[str, bytes]:
    drafts = special_folder(m, "drafts")
    _examine(m, drafts)
    raw, _ = _fetch_raw(m, uid)
    if raw is None:
        raise BridgeError(f"No draft with UID {uid} in {drafts}. Only messages in Drafts can be changed.")
    return drafts, raw


def get_draft(uid: int) -> dict:
    m = _connect()
    try:
        drafts, raw = _draft_raw(m, uid)
    finally:
        _logout(m)
    out = parse_message(raw)
    out.update({"uid": int(uid), "folder": drafts})
    return out


def update_draft(uid: int, *, to: str | None = None, cc: str | None = None,
                 bcc: str | None = None, subject: str | None = None,
                 body: str | None = None, html: str | None = None,
                 add_attachments=(), drop_attachments: bool = False,
                 from_addr: str | None = None) -> dict:
    """Replace a draft: build the new version (unchanged fields kept, thread
    headers kept), APPEND it, then delete the old one. Drafts folder only."""
    m = _connect()
    try:
        drafts, raw = _draft_raw(m, uid)
        old = email.message_from_bytes(raw)
        p = parse_message(raw)
        plain = ""
        for part in old.walk():
            if (not part.is_multipart() and part.get_content_type() == "text/plain"
                    and not part.get_filename()):
                plain = _part_text(part)
                break
        new = build_email(
            from_addr=(from_header(from_addr) if from_addr
                       else str(old.get("From") or smtp_settings()[2] or cfg.proton_imap_user)),
            to=p["to"] if to is None else to,
            cc=p["cc"] if cc is None else cc,
            bcc=_decode(old.get("Bcc")) if bcc is None else bcc,
            subject=p["subject"] if subject is None else subject,
            body=(plain or p["body"]) if body is None else body,
            html=_html_body(raw) if html is None else html,
            attachment_paths=add_attachments,
            extra_attachments=() if drop_attachments else _attachment_parts(raw),
            in_reply_to=(old.get("In-Reply-To") or "").strip(),
            references=" ".join((old.get("References") or "").split()),
        )
        if not new.get("In-Reply-To"):
            t = lookup_thread(old)
            if t:
                new["In-Reply-To"] = t["in_reply_to"]
                new["References"] = t["references"]
                if t.get("forward_id"):
                    new["X-Forwarded-Message-Id"] = t["forward_id"]
        remember_thread(new)
        new_uid = _append_draft(m, drafts, new)
        _expunge_uids(m, drafts, [uid])
        return {"folder": drafts, "old_uid": int(uid), "uid": new_uid}
    finally:
        _logout(m)


def prepare_draft_send(uid: int, orig_uid: int = 0,
                       orig_folder: str = "INBOX") -> tuple[Message, dict]:
    """The draft as it would be sent: its current content (incl. edits made in
    Proton), with thread headers restored if the bridge dropped them. Passing
    orig_uid/orig_folder links it to that message explicitly."""
    m = _connect()
    try:
        drafts, raw = _draft_raw(m, uid)
        orig = None
        if orig_uid:
            folder = resolve_folder(m, orig_folder)
            _examine(m, folder)
            orig, _ = _fetch_raw(m, orig_uid)
            if orig is None:
                raise BridgeError(f"No message with UID {orig_uid} in {orig_folder}.")
    finally:
        _logout(m)
    msg = email.message_from_bytes(raw, policy=policy.SMTP)
    info = {"folder": drafts, "uid": int(uid), "threaded": False, "restored": False}
    if orig is not None:
        ctx = thread_context(orig, mode="reply", quote=False)
        for h, v in (("In-Reply-To", ctx["in_reply_to"]), ("References", ctx["references"])):
            del msg[h]
            if v:
                msg[h] = v
        info["restored"] = True
    elif not msg.get("In-Reply-To"):
        t = lookup_thread(msg)
        if t:
            msg["In-Reply-To"] = t["in_reply_to"]
            if t.get("references") and not msg.get("References"):
                msg["References"] = t["references"]
            if t.get("forward_id") and not msg.get("X-Forwarded-Message-Id"):
                msg["X-Forwarded-Message-Id"] = t["forward_id"]
            info["restored"] = True
    info["threaded"] = bool(msg.get("In-Reply-To"))
    if not msg.get("To"):
        raise BridgeError(f"Draft UID {uid} has no recipient (To).")
    return msg, info


def send_draft(uid: int, orig_uid: int = 0, orig_folder: str = "INBOX") -> dict:
    """Send a draft through the bridge SMTP, then remove it from Drafts."""
    msg, info = prepare_draft_send(uid, orig_uid, orig_folder)
    send(msg)
    m = _connect()
    try:
        _expunge_uids(m, info["folder"], [uid])
        info["draft_removed"] = True
    except BridgeError as e:
        info["draft_removed"] = f"no ({e})"
    finally:
        _logout(m)
    info.update({"to": str(msg.get("To")), "subject": str(msg.get("Subject")),
                 "from": str(msg.get("From"))})
    return info


def delete_draft(uid: int) -> dict:
    m = _connect()
    try:
        drafts, raw = _draft_raw(m, uid)
        subject = parse_message(raw)["subject"]
        _expunge_uids(m, drafts, [uid])
        return {"folder": drafts, "uid": int(uid), "subject": subject}
    finally:
        _logout(m)


# ── Flags, move, labels ───────────────────────────────────────────────────────

FLAG_ACTIONS: dict[str, tuple[str, str]] = {
    "read": ("+FLAGS.SILENT", r"(\Seen)"),
    "unread": ("-FLAGS.SILENT", r"(\Seen)"),
    "star": ("+FLAGS.SILENT", r"(\Flagged)"),
    "unstar": ("-FLAGS.SILENT", r"(\Flagged)"),
}


def describe(folder: str, uids) -> dict:
    """Read-only: subject/from/date of these UIDs (for confirm previews)."""
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        _examine(m, folder)
        typ, data = m.uid("FETCH", uid_set(uids),
                          "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        rows = []
        for item in data or []:
            if isinstance(item, tuple):
                hm = email.message_from_bytes(item[1])
                u = _UID_RE.search(item[0])
                rows.append({"uid": int(u.group(1)) if u else None,
                             "from": _decode(hm.get("From")),
                             "subject": _decode(hm.get("Subject")),
                             "date": _decode(hm.get("Date"))})
        return {"folder": folder, "messages": rows}
    finally:
        _logout(m)


def set_flags(folder: str, uids, action: str) -> dict:
    if action not in FLAG_ACTIONS:
        raise BridgeError(f"Unknown action '{action}'. Use one of: {', '.join(FLAG_ACTIONS)}")
    op, flag = FLAG_ACTIONS[action]
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        _select_rw(m, folder)
        typ, data = m.uid("STORE", uid_set(uids), op, flag)
        if typ != "OK":
            raise BridgeError(f"Changing flags failed: {data!r}")
        return {"folder": folder, "uids": [int(u) for u in uids], "action": action}
    finally:
        _logout(m)


def move_messages(folder: str, uids, dest: str) -> dict:
    """UID MOVE. ``dest`` may be a role ('archive', 'trash', 'spam', 'inbox') or a
    real folder name. Moving to Labels/… is refused: use label_messages."""
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        dest_real = "INBOX" if dest.strip().lower() == "inbox" else resolve_folder(m, dest)
        if dest_real.startswith("Labels/"):
            raise BridgeError("Labels are added with proton_label_messages, not moved to.")
        if dest_real == folder:
            raise BridgeError("Source and destination are the same folder.")
        if not _folder_exists(m, dest_real):
            raise BridgeError(f"Destination folder not found: {dest}")
        if "MOVE" not in _caps(m):
            raise BridgeError("Bridge lacks the MOVE extension; nothing changed.")
        _select_rw(m, folder)
        typ, data = m.uid("MOVE", uid_set(uids), _quote(dest_real))
        if typ != "OK":
            raise BridgeError(f"Move failed: {data!r}")
        return {"folder": folder, "dest": dest_real, "uids": [int(u) for u in uids]}
    finally:
        _logout(m)


def label_messages(folder: str, uids, label: str, remove: bool = False) -> dict:
    """Add (UID COPY into Labels/X) or remove (expunge the copy in Labels/X) a
    label. Never touches the message in its own folder."""
    label = label.strip()
    if not label.startswith("Labels/"):
        label = "Labels/" + label
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        if not _folder_exists(m, label):
            raise BridgeError(f"Label not found: {label} (create it in Proton first).")
        s = uid_set(uids)
        if not remove:
            _examine(m, folder)
            typ, data = m.uid("COPY", s, _quote(label))
            if typ != "OK":
                raise BridgeError(f"Adding label failed: {data!r}")
            return {"folder": folder, "label": label, "uids": [int(u) for u in uids], "removed": False}
        # Map source UIDs to the label folder by Message-ID.
        _examine(m, folder)
        typ, data = m.uid("FETCH", s, "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        ids = []
        for item in data or []:
            if isinstance(item, tuple):
                mid = (email.message_from_bytes(item[1]).get("Message-ID") or "").strip()
                if mid:
                    ids.append(mid)
        _examine(m, label)
        targets: list[str] = []
        for mid in ids:
            typ, d = m.uid("SEARCH", None, "HEADER Message-ID " + _imap_str(mid))
            targets += (d[0] or b"").decode().split() if typ == "OK" and d else []
        if targets:
            _expunge_uids(m, label, targets)
        return {"folder": folder, "label": label, "uids": [int(u) for u in uids],
                "removed": True, "label_uids": [int(t) for t in targets]}
    finally:
        _logout(m)


def create_folder(name: str) -> str:
    """Create a Proton folder (Folders/…) or label (Labels/…)."""
    name = name.strip().strip("/")
    if not name.startswith(("Folders/", "Labels/")) or len(name.split("/", 1)[1].strip()) == 0:
        raise BridgeError("Name must start with Folders/ or Labels/, e.g. Labels/Sponsors.")
    m = _connect()
    try:
        if _folder_exists(m, name):
            raise BridgeError(f"{name} already exists.")
        typ, data = m.create(_quote(name))
        if typ != "OK":
            raise BridgeError(f"Creating {name} failed: {data!r}")
        return name
    finally:
        _logout(m)


# ── Attachments out ───────────────────────────────────────────────────────────

def fetch_attachments(folder: str, uid: int, index: int = 0, name: str = "") -> tuple[str, list[dict]]:
    """(resolved folder, [{name, type, data}]) for one message, read-only (BODY.PEEK).
    index is 1-based; 0 with no name = all attachments. Names match case-insensitively,
    ignoring surrounding whitespace."""
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        _examine(m, folder)
        raw, _ = _fetch_raw(m, uid)
    finally:
        _logout(m)
    if raw is None:
        raise BridgeError(f"No message with UID {uid} in {folder}.")
    parts = _attachment_parts(raw)
    if not parts:
        raise BridgeError("That message has no attachments.")
    if index:
        if not 1 <= index <= len(parts):
            raise BridgeError(f"Attachment {index} does not exist (message has {len(parts)}).")
        parts = [parts[index - 1]]
    elif name:
        want = _decode(name).lower()
        hits = [p for p in parts if p["name"].lower() == want]
        if not hits:
            raise BridgeError(f"No attachment named '{name}'. It has: "
                              + ", ".join(p["name"] for p in parts))
        parts = hits
    return folder, parts


def download_attachment(folder: str, uid: int, dest_dir: str, index: int = 0,
                        name: str = "") -> list[dict]:
    """Save attachment(s) of one message into ``dest_dir`` (allowed dirs only).
    index is 1-based; 0 with no name = all attachments. Never overwrites."""
    real_dir = check_path(dest_dir)
    if not os.path.isdir(real_dir):
        raise BridgeError(f"Not a directory: {dest_dir}")
    folder, parts = fetch_attachments(folder, uid, index, name)
    saved = []
    for p in parts:
        fname = _safe_filename(p["name"])
        stem, ext = os.path.splitext(fname)
        target = os.path.join(real_dir, fname)
        n = 1
        while os.path.exists(target):
            target = os.path.join(real_dir, f"{stem} ({n}){ext}")
            n += 1
        check_path(target)
        with open(target, "xb") as fh:
            fh.write(p["data"])
        saved.append({"name": p["name"], "path": target, "size": len(p["data"])})
    return saved


# ── Threads ───────────────────────────────────────────────────────────────────

def _msgids(v: str | None) -> list[str]:
    return re.findall(r"<[^<>\s]+>", v or "")


def group_threads(rows: list[dict]) -> list[list[dict]]:
    """Union-find over Message-ID / In-Reply-To / References; messages with no
    thread headers join on the same base subject (if it is not tiny)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for r in rows:
        key = f"uid:{r['uid']}"
        find(key)
        for mid in r["ids"]:
            union(key, f"id:{mid}")
        bs = base_subject(r["subject"]).lower()
        if len(bs) >= 8:
            union(key, f"subj:{bs}")
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(find(f"uid:{r['uid']}"), []).append(r)
    out = [sorted(g, key=lambda r: r["ts"]) for g in groups.values()]
    out.sort(key=lambda g: g[-1]["ts"], reverse=True)
    return out


def _thread_rows(m: imaplib.IMAP4, folder: str, scan: int, criteria: dict) -> list[dict]:
    _examine(m, folder)
    typ, data = m.uid("SEARCH", None, build_search(**criteria))
    uids = (data[0] or b"").split() if typ == "OK" and data else []
    uids = sorted(uids, key=int, reverse=True)[:scan]
    rows: list[dict] = []
    if not uids:
        return rows
    typ, data = m.uid("FETCH", b",".join(uids).decode(),
                      "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])")
    for item in data or []:
        if not isinstance(item, tuple):
            continue
        hm = email.message_from_bytes(item[1])
        u = _UID_RE.search(item[0])
        fl = _FLAGS_RE.search(item[0])
        date = _decode(hm.get("Date"))
        try:
            ts = parsedate_to_datetime(date).timestamp() if date else 0.0
        except Exception:  # noqa: BLE001
            ts = 0.0
        own = _msgids(hm.get("Message-ID"))
        rows.append({
            "uid": int(u.group(1)) if u else 0,
            "from": _decode(hm.get("From")), "to": _decode(hm.get("To")),
            "subject": _decode(hm.get("Subject")), "date": date, "ts": ts,
            "flags": fl.group(1).decode() if fl else "",
            "ids": own + _msgids(hm.get("In-Reply-To")) + _msgids(hm.get("References")),
        })
    return rows


def list_threads(folder: str = "", limit: int = 20, scan: int = 300, **criteria) -> dict:
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        rows = _thread_rows(m, folder, scan, criteria)
    finally:
        _logout(m)
    threads = group_threads(rows)
    out = []
    for g in threads[:limit]:
        people: list[str] = []
        for r in g:
            for _, a in _addr_list(r["from"]):
                if a not in people:
                    people.append(a)
        out.append({"subject": base_subject(g[0]["subject"]) or g[0]["subject"],
                    "count": len(g), "last_date": g[-1]["date"],
                    "unread": sum("\\Seen" not in r["flags"] for r in g),
                    "participants": people, "uids": [r["uid"] for r in g]})
    return {"folder": folder, "scanned": len(rows), "threads": out}


def read_thread(uid: int, folder: str = "", scan: int = 500) -> dict:
    """All messages in the same thread as ``uid`` (within ``folder``; default
    All Mail so your sent replies are included), oldest first, read-only."""
    m = _connect()
    try:
        folder = resolve_folder(m, folder)
        rows = _thread_rows(m, folder, scan, {})
        group = next((g for g in group_threads(rows) if any(r["uid"] == int(uid) for r in g)), None)
        if group is None:
            raise BridgeError(f"No message with UID {uid} among the newest {scan} in {folder}.")
        msgs = []
        for r in group:
            raw, flags = _fetch_raw(m, r["uid"])
            if raw is None:
                continue
            p = parse_message(raw)
            p.update({"uid": r["uid"], "flags": flags})
            msgs.append(p)
        return {"folder": folder, "messages": msgs}
    finally:
        _logout(m)


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

"""Conservative secret redaction for file content surfaced to the model.

Reading a config/.env-style file inside an allowed path would otherwise drop
live API keys, passwords, and tokens straight into the MCP transcript (and any
logs that capture it). `redact_secrets` masks the *values* of secret-looking
assignments and auth headers while leaving structure (keys, comments, normal
prose) intact, so the output is still useful.

Line-based and deliberately narrow to avoid mangling ordinary text.
"""
from __future__ import annotations

import re

# Key names whose assigned value should be masked.
_SECRET_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|auth|credential|bearer)"
)
# KEY = value  /  KEY: value   (env files, yaml, ini, json-ish)
_ASSIGN = re.compile(r"^(\s*[\"']?[A-Za-z0-9_.\-]+[\"']?\s*[:=]\s*)(.+?)(\s*)$")
# Authorization: Bearer xxxxx  (and "token <x>")
_AUTH_HEADER = re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+)(\S+)")

_MASK = "***REDACTED***"


def redact_secrets(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions)."""
    if not text:
        return text, 0
    count = 0
    out_lines: list[str] = []
    in_pem = False
    for line in text.splitlines():
        stripped = line.strip()

        # PEM / private key blocks: mask the body between BEGIN/END markers.
        if "-----BEGIN" in line and "PRIVATE KEY-----" in line:
            in_pem = True
            out_lines.append(line)
            continue
        if in_pem:
            if "-----END" in line and "KEY-----" in line:
                in_pem = False
                out_lines.append(line)
            else:
                if stripped:
                    count += 1
                out_lines.append(_MASK)
            continue

        # Comments pass through untouched.
        if stripped.startswith("#") or stripped.startswith("//"):
            out_lines.append(line)
            continue

        m = _ASSIGN.match(line)
        if m and _SECRET_KEY.search(m.group(1)) and m.group(2).strip():
            out_lines.append(f"{m.group(1)}{_MASK}{m.group(3)}")
            count += 1
            continue

        new_line, n = _AUTH_HEADER.subn(lambda mm: mm.group(1) + _MASK, line)
        if n:
            count += n
            out_lines.append(new_line)
            continue

        out_lines.append(line)

    return "\n".join(out_lines), count

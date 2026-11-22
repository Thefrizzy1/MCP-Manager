#!/usr/bin/env python3
"""plutus_node.py — the small program that makes a machine a Plutus node.

It only makes outgoing connections to the Plutus server. Every half minute it
reports the machine's resources, asks for its next job, does it, and reports
the result. It can do a few fixed kinds of job and nothing else:

  wake        send a wake-on-LAN packet to another machine on this network
  comfy_test  run given prompts on this machine's ComfyUI and measure time and VRAM

When the server says the machine may power off (the server switched it on, its
work is done) and nobody has touched mouse or keyboard for a while, it shuts
the machine down. A machine a person switched on is never shut down.

    python plutus_node.py            run forever (what the service does)
    python plutus_node.py once       one report + one job, then exit (for testing)

Standard library only. Windows, Linux and macOS.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "plutus_mcp": "http://localhost:8765/mcp",
    "machine": socket.gethostname().lower(),
    "kinds": ["comfy_test"],            # what this node does: "wake", "comfy_test"
    "report": True,                     # show this machine on the Overview page
    "comfy_dir": "",                    # ComfyUI folder (holds main.py, models/, input/)
    "comfy_python": "",                 # python that runs ComfyUI; "" = same folder's venv or python
    "comfy_args": [],
    "comfy_url": "http://127.0.0.1:8188",
    "inputs_dir": "",                   # where the standard test inputs are (e.g. a share on the NAS)
    "delete_downloads": True,           # remove models a test downloaded once it is done
    "idle_minutes": 15,                 # no input for this long before a power-off is allowed
    "poll_seconds": 30,
    "disk_path": "",
}
WIN = platform.system() == "Windows"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = Path(os.getenv("PLUTUS_NODE_CONFIG", HERE / "node.json"))
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ── talking to Plutus (MCP over HTTP) ────────────────────────────────────────
def plutus(cfg: dict, tool: str, args: dict | None = None, timeout: int = 40) -> str:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if os.getenv("PLUTUS_MCP_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["PLUTUS_MCP_TOKEN"]

    def post(body: dict, sid: str = ""):
        h = dict(headers, **({"Mcp-Session-Id": sid} if sid else {}))
        req = urllib.request.Request(cfg["plutus_mcp"], json.dumps(body).encode(), h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
            m = re.search(r"\{.*\}", text, re.S)
            return r.headers.get("Mcp-Session-Id", sid), (json.loads(m.group(0)) if m else {})

    sid, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "plutus-node", "version": "1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": tool, "arguments": {"params": args or {}}}}, sid)
    if "error" in res:
        raise RuntimeError(f"{tool}: {res['error']}")
    return "\n".join(c.get("text", "") for c in res.get("result", {}).get("content", []))


# ── this machine ─────────────────────────────────────────────────────────────
def gpu_now() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15).stdout
        name, total, used, util = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"gpu": name.replace("NVIDIA ", "").replace("GeForce ", ""), "vram_gb": round(int(total) / 1024),
                "vram_used_gb": round(int(used) / 1024, 1), "gpu_util": int(util)}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return {}


def ram_gb() -> int:
    try:
        if WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return round(ms.ullTotalPhys / 1e9)
        with open("/proc/meminfo") as fh:
            return round(int(next(l for l in fh if l.startswith("MemTotal:")).split()[1]) / 1e6)
    except Exception:
        return 0


def boot_ts() -> int:
    try:
        if WIN:
            import ctypes
            return int(time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000)
        with open("/proc/uptime") as fh:
            return int(time.time() - float(fh.read().split()[0]))
    except Exception:
        return 0


def idle_seconds() -> float:
    """Seconds since the last mouse or keyboard input. Unknown counts as 'just now'."""
    if not WIN:
        return 1e9                                  # servers: nobody sits at them
    try:
        import ctypes

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = LII()
        lii.cbSize = ctypes.sizeof(LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:
        return 0.0


def lan_mac(cfg: dict) -> str:
    """MAC of the network adapter this machine uses to reach the server.

    That is the adapter a wake packet has to hit. A machine with several
    adapters (VPN, virtual switches) must not report one of the others.
    """
    try:
        host = re.sub(r"^https?://", "", cfg["plutus_mcp"]).split("/")[0].split(":")[0]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, 9))
            ip = sock.getsockname()[0]
        if WIN:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-NetIPAddress -IPAddress {ip} | Get-NetAdapter).MacAddress"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
            if re.fullmatch(r"([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}", out):
                return out.replace("-", ":").upper()
        else:
            out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
            dev = next((l.split()[1] for l in out.splitlines() if f" {ip}/" in l), "")
            if dev:
                return Path(f"/sys/class/net/{dev}/address").read_text().strip().upper()
    except Exception:
        pass
    return ":".join(f"{(uuid.getnode() >> sh) & 0xff:02X}" for sh in range(40, -1, -8))


def specs(cfg: dict) -> dict:
    out = {"cores": os.cpu_count() or 0, "ram_gb": ram_gb(), "boot_ts": boot_ts(),
           "mac": lan_mac(cfg),
           "os": f"{platform.system()} {platform.release()}"}
    try:
        du = shutil.disk_usage(cfg["disk_path"] or cfg["comfy_dir"] or str(HERE))
        out.update(disk_free_gb=round(du.free / 1e9), disk_total_gb=round(du.total / 1e9))
    except OSError:
        pass
    out.update(gpu_now())
    return out


# ── job: wake ────────────────────────────────────────────────────────────────
def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", mac))
    if len(raw) != 6:
        raise ValueError("MAC address must have 6 bytes")
    return b"\xff" * 6 + raw * 16


def do_wake(cfg: dict, payload: dict) -> dict:
    packet = magic_packet(payload["mac"])
    targets = [payload.get("broadcast") or "255.255.255.255"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for _ in range(3):
            for t in targets:
                for port in (9, 7):
                    s.sendto(packet, (t, port))
            time.sleep(0.4)
    finally:
        s.close()
    return {"sent": True, "target": payload.get("target", "")}


# ── job: comfy_test ──────────────────────────────────────────────────────────
def comfy_get(cfg: dict, path: str, timeout: int = 10):
    with urllib.request.urlopen(cfg["comfy_url"].rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def comfy_up(cfg: dict) -> bool:
    try:
        comfy_get(cfg, "/system_stats", 4)
        return True
    except Exception:
        return False


def start_comfy(cfg: dict):
    """Start ComfyUI if it is not answering. Returns the process we started, or None."""
    if comfy_up(cfg):
        return None
    cdir = Path(cfg["comfy_dir"])
    if not (cdir / "main.py").is_file():
        raise RuntimeError(f"ComfyUI not found: set comfy_dir (looked in {cdir or 'nothing set'})")
    py = cfg["comfy_python"]
    if not py:
        for cand in (cdir / "venv" / ("Scripts/python.exe" if WIN else "bin/python"),
                     cdir.parent / "python_embeded" / "python.exe", cdir / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")):
            if cand.is_file():
                py = str(cand)
                break
    port = cfg["comfy_url"].rsplit(":", 1)[-1].strip("/")
    logf = open(HERE / "comfyui.log", "w")
    proc = subprocess.Popen([py or sys.executable, "main.py", "--port", port, *cfg["comfy_args"]],
                            cwd=str(cdir), stdout=logf, stderr=subprocess.STDOUT)
    for _ in range(150):
        if proc.poll() is not None:
            raise RuntimeError("ComfyUI stopped while starting; see comfyui.log next to the node")
        if comfy_up(cfg):
            return proc
        time.sleep(2)
    proc.terminate()
    raise RuntimeError("ComfyUI did not answer within 5 minutes")


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "plutus-node"})
    if os.getenv("HF_TOKEN") and "huggingface.co" in url:
        req.add_header("Authorization", "Bearer " + os.environ["HF_TOKEN"])
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh, 4 * 1024 * 1024)
    tmp.replace(dest)


def find_model(models: Path, dest: str, name: str) -> Path | None:
    base = models / dest
    if (base / name).is_file():
        return base / name
    hit = next(base.rglob(name), None) if base.is_dir() else None
    return hit


def do_comfy_test(cfg: dict, payload: dict) -> dict:
    cdir = Path(cfg["comfy_dir"])
    fetched: list[Path] = []
    started = None
    result: dict = {"runs": [], "gpu": gpu_now().get("gpu", "")}
    try:
        for m in payload.get("models") or []:
            if not m.get("required", True) or find_model(cdir / "models", m["dest"], m["name"]):
                continue
            target = cdir / "models" / m["dest"] / m["name"]
            log(f"downloading {m['name']}")
            download(m["url"], target)
            fetched.append(target)
        for name in payload.get("inputs") or []:
            src = Path(cfg["inputs_dir"]) / name
            if not src.is_file():
                raise RuntimeError(f"standard input missing: {name} (looked in {cfg['inputs_dir'] or 'inputs_dir not set'})")
            if not (cdir / "input" / name).is_file():
                shutil.copy2(src, cdir / "input" / name)
        started = start_comfy(cfg)
        for p in payload.get("prompts") or []:
            run = {"name": p.get("name", "run"), "ok": False}
            t0 = time.time()
            peak = 0.0
            try:
                req = urllib.request.Request(cfg["comfy_url"].rstrip("/") + "/prompt",
                                             json.dumps({"prompt": p["prompt"], "client_id": "plutus-node"}).encode(),
                                             {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    pid = json.loads(r.read().decode())["prompt_id"]
                limit = t0 + float(payload.get("timeout_min", 45)) * 60
                while True:
                    peak = max(peak, gpu_now().get("vram_used_gb", 0.0))
                    hist = comfy_get(cfg, f"/history/{pid}").get(pid)
                    if hist:
                        status = hist.get("status") or {}
                        run["ok"] = status.get("status_str") == "success" or (status.get("completed") is True)
                        if not run["ok"]:
                            msgs = [m for m in status.get("messages") or [] if m and m[0] == "execution_error"]
                            run["error"] = str((msgs[0][1] if msgs else status).get("exception_message", status))[:400] \
                                if msgs else str(status)[:400]
                        break
                    if time.time() > limit:
                        run["error"] = "timed out"
                        break
                    time.sleep(2)
            except Exception as e:
                run["error"] = str(e)[:400]
            run.update(seconds=round(time.time() - t0, 1), peak_vram_gb=round(peak, 1))
            result["runs"].append(run)
            log(f"{run['name']}: {'ok' if run['ok'] else 'FAILED'} in {run['seconds']} s, peak {run['peak_vram_gb']} GB")
    finally:
        if started:
            started.terminate()
            try:
                started.wait(timeout=60)
            except subprocess.TimeoutExpired:
                started.kill()
        if cfg["delete_downloads"]:
            for f in fetched:
                try:
                    f.unlink()
                except OSError:
                    pass
            result["deleted_downloads"] = len(fetched)
    result["ok"] = bool(result["runs"]) and all(r["ok"] for r in result["runs"])
    return result


HANDLERS = {"wake": do_wake, "comfy_test": do_comfy_test}


# ── the loop ─────────────────────────────────────────────────────────────────
def power_off(cfg: dict) -> None:
    log("work is done and nobody is at the machine: shutting down in 60 seconds")
    if WIN:
        subprocess.run(["shutdown", "/s", "/t", "60", "/c", "Plutus: night work is done. Run 'shutdown /a' to cancel."])
    else:
        subprocess.run(["systemctl", "poweroff"])


def tick(cfg: dict, doing: str = "") -> str:
    """One round: report, ask, work. Returns 'job', 'idle' or 'poweroff'."""
    if cfg["report"]:
        plutus(cfg, "machine_report", {"machine": cfg["machine"], "specs_json": json.dumps(specs(cfg)),
                                       "doing": doing or " "})
    ask = json.loads(plutus(cfg, "node_next_job", {"machine": cfg["machine"], "kinds_json": json.dumps(cfg["kinds"]),
                                                   "boot_ts": boot_ts()}) or "{}")
    job = ask.get("job")
    if not job:
        if ask.get("may_power_off") and idle_seconds() >= cfg["idle_minutes"] * 60:
            return "poweroff"
        return "idle"
    kind = job["kind"]
    log(f"job {job['id']}: {kind}")
    if cfg["report"]:
        label = f"Testing {job['payload'].get('slug', '')} on this card" if kind == "comfy_test" else kind
        plutus(cfg, "machine_report", {"machine": cfg["machine"], "doing": label})
    try:
        if kind not in HANDLERS or kind not in cfg["kinds"]:
            raise RuntimeError(f"this node does not do {kind!r}")
        result = HANDLERS[kind](cfg, job["payload"])
        ok = bool(result.get("ok", True))
    except Exception as e:
        result, ok = {"error": str(e)[:600]}, False
        log(f"job failed: {e}")
    plutus(cfg, "node_job_done", {"job_id": job["id"], "ok": ok, "result_json": json.dumps(result)[:19000]})
    return "job"


def main() -> int:
    cfg = load_config()
    once = len(sys.argv) > 1 and sys.argv[1] == "once"
    log(f"node {cfg['machine']} -> {cfg['plutus_mcp']} (does: {', '.join(cfg['kinds'])})")
    while True:
        try:
            state = tick(cfg)
        except Exception as e:
            state = "error"
            log(f"could not reach Plutus or finish the round: {e}")
        if once:
            print(state)
            return 0 if state != "error" else 1
        if state == "poweroff":
            power_off(cfg)
            time.sleep(300)
        elif state != "job":                       # after a job, ask again at once
            time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    sys.exit(main())

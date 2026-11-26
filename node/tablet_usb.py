#!/usr/bin/env python3
"""Android tablet on a USB cable: opens the Plutus teleprompter on it and lets the computer control it.

    python3 tablet_usb.py --plutus HOST:8766            run now
    python3 tablet_usb.py --plutus HOST:8766 --install  run at every login (macOS)
    python3 tablet_usb.py --remove                      undo --install

When the tablet is plugged in: the screen stays on while the cable is in, the
teleprompter opens in the tablet's browser (over the cable, so no Wi-Fi is
needed), and a window with the tablet's screen opens on the computer, which
mouse and keyboard can control. Needs adb and scrcpy, and USB debugging allowed
once on the tablet. No passwords are stored: the tablet signs in to Plutus itself.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

PORT = 8799             # same port on computer and tablet; "localhost" lets the browser hold the screen on
LABEL = "com.plutus.tablet"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
HOME_DIR = Path.home() / "Library/Application Support/PlutusTablet"


def tool(name: str) -> str:
    found = shutil.which(name)
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if not found and os.access(f"{d}/{name}", os.X_OK):
            found = f"{d}/{name}"
    return found or ""


def pump(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:
        pass
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def serve(server: socket.socket, host: str, port: int) -> None:
    """Pass connections from this computer's localhost on to Plutus."""
    def one(client: socket.socket) -> None:
        try:
            up = socket.create_connection((host, port), timeout=10)
        except OSError:
            client.close()
            return
        up.settimeout(None)
        back = threading.Thread(target=pump, args=(up, client), daemon=True)
        back.start()
        pump(client, up)
        back.join()
        client.close()
        up.close()

    while True:
        try:
            client, _ = server.accept()
        except OSError:
            return
        threading.Thread(target=one, args=(client,), daemon=True).start()


def plugged(listing: str) -> tuple[list[str], list[str]]:
    """(ready, waiting for the tablet's OK) out of ``adb devices -l``. USB only."""
    ready, waiting = [], []
    for line in listing.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or ":" in parts[0] or parts[0].startswith("emulator-"):
            continue
        (ready if parts[1] == "device" else waiting if parts[1] == "unauthorized" else []).append(parts[0])
    return ready, waiting


def adb(*args: str) -> str:
    try:
        return subprocess.run([tool("adb"), *args], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def set_up(serial: str, mirror: bool) -> "subprocess.Popen | None":
    adb("-s", serial, "reverse", f"tcp:{PORT}", f"tcp:{PORT}")
    adb("-s", serial, "shell", "svc", "power", "stayon", "usb")
    adb("-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    # Straight to Chrome when it is there: otherwise Android may ask which browser to use.
    chrome = "package:com.android.chrome" in adb("-s", serial, "shell", "pm", "list", "packages", "com.android.chrome")
    adb("-s", serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        *(["-p", "com.android.chrome"] if chrome else []), "-d", f"http://localhost:{PORT}/app#/prompter")
    if mirror and tool("scrcpy"):
        env = {**os.environ, "ADB": tool("adb")}
        return subprocess.Popen([tool("scrcpy"), "-s", serial, "--stay-awake", "--keyboard=uhid", "--no-audio",
                                 "--window-title", "Teleprompter tablet"],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return None


def run(plutus: str, mirror: bool) -> None:
    host, _, port = plutus.rpartition(":")
    server = socket.create_server(("127.0.0.1", PORT))
    threading.Thread(target=serve, args=(server, host, int(port)), daemon=True).start()
    here: dict[str, "subprocess.Popen | None"] = {}
    said = ""
    while True:
        if not tool("adb"):
            say = "adb is missing: brew install scrcpy && brew install --cask android-platform-tools"
        else:
            ready, waiting = plugged(adb("devices", "-l"))
            for serial in [s for s in here if s not in ready]:
                here.pop(serial)
            for serial in ready:
                if serial not in here:           # once per plug-in: a window you close stays closed
                    here[serial] = set_up(serial, mirror)
            say = (f"tablet connected ({len(here)})" if here else
                   "allow USB debugging on the tablet's screen" if waiting else "no tablet plugged in")
        if say != said:
            print(say, flush=True)
            said = say
        time.sleep(3)


def install(plutus: str, mirror: bool) -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    script = HOME_DIR / "tablet_usb.py"
    if Path(__file__).resolve() != script.resolve():
        shutil.copy(__file__, script)
    args = ["/usr/bin/python3", str(script), "--plutus", plutus] + ([] if mirror else ["--no-window"])
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps({
        "Label": LABEL, "ProgramArguments": args, "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 20,
        "StandardOutPath": str(HOME_DIR / "tablet.log"), "StandardErrorPath": str(HOME_DIR / "tablet.log")}))
    dom = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", dom, str(PLIST)], check=True)
    print("Installed. It runs at every login; the log is", HOME_DIR / "tablet.log")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plutus", default="", help="HOST:PORT of the Plutus dashboard")
    ap.add_argument("--no-window", action="store_true", help="do not show the tablet's screen on the computer")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    if a.remove:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        PLIST.unlink(missing_ok=True)
        print("Removed.")
        return
    if not a.plutus.rpartition(":")[2].isdigit() or not a.plutus.rpartition(":")[0]:
        sys.exit("--plutus HOST:PORT is needed, for example --plutus 10.0.0.5:8766")
    (install if a.install else run)(a.plutus, not a.no_window)


if __name__ == "__main__":
    main()

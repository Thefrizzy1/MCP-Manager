# Plutus node

The small program that turns a machine into a worker's hands. It reports the
machine to the Overview page, asks the Plutus server for work, does it and
reports back. Outgoing connections only; standard-library Python 3.9+.

## What a node can do

| Kind | What happens |
|---|---|
| `wake` | Sends a wake-on-LAN packet to another machine on the same network. Run this on a machine that is always on, on the host itself (not in a bridged container). |
| `comfy_test` | Starts ComfyUI if needed, fetches missing models, runs the given prompts with the standard inputs, measures seconds and peak VRAM, and removes the models it fetched. |

It powers the machine off only when all of this is true: the server woke it,
its queue is empty, the machine's work hours allow power-off, and nobody has
touched mouse or keyboard for `idle_minutes`.

## Set up

1. Copy `plutus_node.py` and a `node.json` next to it:

   ```json
   {
     "plutus_mcp": "http://YOUR-PLUTUS-HOST:8765/mcp",
     "machine": "windows-pc",
     "kinds": ["comfy_test"],
     "comfy_dir": "C:/path/to/ComfyUI",
     "inputs_dir": "//NAS/share/Lab Inputs"
   }
   ```

2. Try it: `python plutus_node.py once` prints `idle`, `job` or `poweroff` and the machine appears on the Overview.
3. Run it at boot.
   - **Windows:** `install_windows.ps1` registers a scheduled task that starts it at boot, whether or not anyone is logged in.
   - **Linux:** copy `plutus-node.service` to `/etc/systemd/system/`, then `systemctl enable --now plutus-node`.
4. On the Overview, click the machine and set its work hours.

Wake-on-LAN also needs the machine itself to allow it: enabled in the BIOS and
on the network adapter, and on Windows with Fast Startup switched off.

## Teleprompter tablet on USB (macOS)

`tablet_usb.py` opens the teleprompter on an Android tablet whenever it is plugged in, keeps its screen on
while the cable is in, and shows the tablet in a window the Mac's mouse and keyboard can control.

1. `brew install scrcpy` and `brew install --cask android-platform-tools`
2. On the tablet: Settings, About tablet, Software information, tap *Build number* seven times; then
   Settings, Developer options, *USB debugging* on. Plug it in and allow this computer.
3. `python3 node/tablet_usb.py --plutus HOST:8766 --install` (undo with `--remove`)

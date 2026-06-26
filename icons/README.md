# Optional local logos

Place files here as **`{service_id}.svg`** or **`.png`** (same id as on the dashboard card, e.g. `jellyfin.svg`, `cust_myapi.png`).

They are served at **`/icons/...`** when this folder exists at startup and override CDN order (see `core/service_logos.py`).

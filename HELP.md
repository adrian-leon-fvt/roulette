# Help: randomness and setup

## Start the application

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
from this folder:

```powershell
uv run roulette.py
```

uv prepares Python and dependencies automatically; no virtual-environment
activation needed. See [README.md](README.md) for platform requirements.

Add names, enable those participating, choose speed and colours, then spin.
Winners sit in **Cooldown** for three subsequent completed spins.
**Return to wheel** puts someone back immediately and clears their counter.
Select anyone in Names and choose **Send to cooldown** to start a three-turn
cooldown manually, without picking them as a winner.
If no one can participate, enable or return someone; blocked spins do not
count as turns.
**Test run** spins without saving a winner, changing cooldowns, or using QRNG.

## Enable quantum randomness

This application uses **[qrngapi.com](https://qrngapi.com)**, the same source
used by `Shuffle-Peeps`. Visit the service and obtain an API key through its
account/key management. Service availability, plans and limits belong to
that provider.

The application requests:

```text
GET https://qrngapi.com/api/random?bytes=N&format=hex
X-API-Key: <your token>
```

Only the byte count is sent; participant names remain local.

Choose either configuration method:

1. Reuse `Shuffle-Peeps`: add `QRNG_TOKEN=your-api-key` on its own line in
   `$HOME\.env`. If already configured, nothing else is needed.
2. Set environment variable `QRNG_TOKEN` before launching. This takes precedence
   over the home `.env` file.

To edit the shared home file without overwriting existing entries:

```powershell
notepad "$HOME\.env"
```

Add or update only this entry:

```dotenv
QRNG_TOKEN=your-api-key
```

Replace the placeholder with your actual key. Restart the application after
changing credentials. Never commit your token; `.env` is ignored, and the app
does not write tokens into `prefs.yml`.

## No token or service unavailable

Spins still work. As in `Shuffle-Peeps`, a missing token or failed quantum
request triggers a non-quantum fallback. This application uses Python's
operating-system-backed randomness rather than a predictable seeded generator.
The UI shows the source and fallback reason; fallback results are not labelled
quantum.

If fallback was unexpected, check the token entry, internet connection,
provider account limits and service status. No PowerShell profile changes
are required.

On managed networks, Python may reject an invalid corporate TLS certificate
even when PowerShell works. The app still falls back safely. Ask your network
administrator to fix the certificate chain; do not disable certificate checks.

## Preferences

`prefs.yml` beside the script holds names, enabled states, cooldown counters,
speed, colours, last run and last winner. It and dependencies are excluded
from Git. Close the application before editing this file. Keep only one
instance running against it.

Existing `prefs.yaml` files are used when no `prefs.yml` exists.
To reset, close the app and move either preferences file elsewhere. Invalid
preferences produce an error instead of being overwritten.

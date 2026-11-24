# Quantum Roulette

Local Python name wheel with quantum-random picks, colourful slices and a
dedicated cooldown space. Names and preferences stay on your machine.

## Run (Windows)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
from this folder:

```powershell
uv run roulette.py
```

uv automatically prepares Python 3.13, the ignored `.venv` and dependencies
from `uv.lock`; no activation or separate install step needed. Tkinter is
required and included in uv's managed Windows Python. On Linux, your Python
installation may need the system Tk package.

VS Code: **Terminal > Run Task > Run roulette**.

## Quantum source

Uses the same service as `Shuffle-Peeps`: `https://qrngapi.com/api/random`.
See [HELP.md](HELP.md) for QRNG account, token and setup instructions.
Existing `Shuffle-Peeps` credentials work unchanged. Tokens are never saved
in preferences.

Only the requested byte count goes to the service, not names. Selection uses
rejection sampling to avoid modulo bias. Without a token, or if the service
fails, picks use operating-system randomness instead. The UI identifies
the source and fallback reason; spins still work offline.
HTTP requests run off the UI thread.

## Names and cooldown

- Add one name per line; remove unwanted names or disable them without deleting.
- Each winner moves into **Cooldown** for the next **three completed spins**.
  Remaining turns appear beside their name.
- **Return to wheel** clears that person's cooldown immediately.
- **Send to cooldown** moves any selected name out of the wheel for three turns,
  without requiring a win. You can return them whenever you choose.
- Manually disabled names stay disabled until enabled.
- Failed or blocked spins do not advance cooldowns. If everyone is cooling
  down or disabled, return or enable someone before spinning.
- **Test run** spins using local randomness without saving a winner or changing
  cooldowns; it does not contact the QRNG service.
- Speed slider changes spin speed; colour scheme changes slice colours.

Preferences save to ignored `prefs.yml` beside the script: names, enabled
state, cooldowns, speed, colour scheme, last run and last winner. The result
is saved before its animation, so closing during animation does not lose it.
Invalid preferences are reported rather than silently replaced; repair or move
the file to reset. Existing `prefs.yaml` files remain supported when no
`prefs.yml` exists. Do not run multiple instances against the same preferences.

## Checks

```powershell
uv run python -m unittest -v
```

Tests use mocked quantum responses, not API credits.

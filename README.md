# Quantum Roulette

Local Python name wheel with quantum-random picks, colourful slices and a
dedicated cooldown space. Names and preferences stay on your machine.

## Run (Windows)

Requires Python 3.11+ with Tkinter (included in the standard Windows installer).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe roulette.py
```

With [uv](https://docs.astral.sh/uv/), setup can instead use:

```powershell
uv venv --python 3.13
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\python.exe roulette.py
```

VS Code: **Terminal > Run Task > Run roulette** after setup.

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
- Manually disabled names stay disabled until enabled.
- Failed or blocked spins do not advance cooldowns. If everyone is cooling
  down or disabled, return or enable someone before spinning.
- Speed slider changes spin speed; colour scheme changes slice colours.

Preferences save to ignored `prefs.yaml` beside the script: names, enabled
state, cooldowns, speed, colour scheme, last run and last winner. The result
is saved before its animation, so closing during animation does not lose it.
Invalid preferences are reported rather than silently replaced; repair or move
the file to reset. Do not run multiple instances against the same preferences.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Tests use mocked quantum responses, not API credits.

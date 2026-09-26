# Installing the clipstash helper (macOS)

The Chrome extension needs a small local helper (`clipstashd`) listening on
`http://127.0.0.1:8787`. This page gets a stranger from a fresh clone to a
green dot in the popup.

## Requirements

- macOS 12 (Monterey) or newer
- Python 3.11 or newer — check with `python3 --version`
- Google Chrome (or another Chromium browser) for the unpacked extension

## 1. Clone and enter the repo

```bash
git clone https://github.com/eriksjaastad/clipstash.git
cd clipstash
```

## 2. Create a virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install the helper

Choose one:

**pip (editable install):**

```bash
pip install -e .
```

**uv:**

```bash
uv sync
```

With `pip install -e .` the `clipstashd` command is on your PATH while the
venv is active. With `uv sync`, prefix commands with `uv run` (for example
`uv run clipstashd`).

## 4. Run the helper

```bash
python -m helper     # module form, no install needed
# or
clipstashd           # installed entry point
```

Keep it running. It binds `127.0.0.1` only (loopback, no external exposure).

Check it in a second terminal:

```bash
curl http://127.0.0.1:8787/health
# {"ok": true, "version": "0.1.0"}
```

## 5. Load the unpacked extension

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top right).
3. Click **Load unpacked** and choose the `extension/` folder in this repo.
4. Click the clipstash toolbar icon. The popup should show a green dot and
   **Helper running (v0.1.0)**.

If the popup shows **Helper not running**, start the helper with step 4, then
click the refresh button (↻) in the popup.

## Optional: start the helper at login

A LaunchAgent template ships in `packaging/macos/`. Follow the
[README section](../README.md#run-the-helper-at-login-macos-optional) for the
install and unload steps.

## Where packets go

Packets are written to `~/Clipstash/packets/<id>/` by default. Override with
`CLIPSTASH_ROOT=/path/to/packets` or `clipstashd --root /path/to/packets`.

## Troubleshooting

- **`python3: command not found`** — install Python 3.11+ from
  [python.org](https://www.python.org/downloads/macos/) or Homebrew
  (`brew install python@3.12`).
- **Port 8787 already in use** — another `clipstashd` is already running.
  Stop it (`Ctrl-C` in its terminal, or `pkill -f clipstashd`) and start again.
  The extension talks to port 8787, so keep the default unless you edit the
  extension too.
- **Helper runs but the popup still shows not running** — open
  `http://127.0.0.1:8787/health` in Chrome. If Chrome can't reach it, check
  that the helper terminal has no error and that no firewall is blocking
  loopback.
- **`clipstashd: command not found`** — your venv isn't active, or you used
  `uv sync` (use `uv run clipstashd`), or the editable install didn't complete.
  Re-run step 3 with the venv active.

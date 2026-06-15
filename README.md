# iTerm2 auto tab namer

Renames iTerm2 tabs based on their content, using Apple's on-device Foundation
Models (free, private, no API key). Tabs in the same project get distinct names —
e.g. "Auth Bug" vs "DB Migration" instead of both saying "myapp".

## Install (Homebrew — recommended)

```sh
brew install EnjoyBacon7/tap/iterm-tab-namer
brew services start iterm-tab-namer
```

One-time setup in iTerm2:

1. **Settings → General → Magic →** enable **Python API**.
2. **iTerm2 menu → Install Shell Integration** (gives the daemon command/cwd
   tracking; iTerm2 adds the line to your shell config itself).
3. **Restart iTerm2** and click **Allow** when it asks to control iTerm2.

To stop or remove:

```sh
brew services stop iterm-tab-namer
brew uninstall iterm-tab-namer
```

Requires macOS 26+ on Apple Silicon (the on-device model is arm64-only).

## Manual install (alternative)

```sh
./install.sh
```

Then **restart iTerm2** and click **Allow** when it asks to authorize the Python API.

The installer builds the Swift binary, enables iTerm2's Python API, installs zsh
shell integration, and runs the daemon at login via a launchd agent.

## How it works

Every `interval` seconds (default 90), the daemon reads each tab's working
directory and recent commands and asks the on-device model — via the `tabnamer`
Swift binary, loaded once and kept resident — for a short label, then composes
the tab name from your `template`. It never overrides a name you set by hand,
skips any tab with a foreground process running (its job name — `vim`, `ssh`,
`npm` — already labels it), and only renames when a tab's content or siblings
change.

Activity log: `~/.config/iterm-tab-namer/tab-namer.log` — one line per sweep
(when it renames anything or was manually triggered) and per rename. It's a
rotating, size-capped file (≈4 MB max), so it can't grow without bound.
`tail -f` it to watch the daemon work. Crash/stderr output still lands in
`/opt/homebrew/var/log/iterm-tab-namer.log`.

## Settings

Configure the daemon with the `iterm-tab-namer` CLI. Settings are read at
startup, so restart the service to apply a change:

```sh
iterm-tab-namer config list                        # show settings + file path
iterm-tab-namer config get template
iterm-tab-namer config set template "{project} - {task}"
iterm-tab-namer config set interval 120
iterm-tab-namer config set enabled false
brew services restart iterm-tab-namer              # apply
```

Settings are stored as JSON at `~/.config/iterm-tab-namer/config.json` (you can
also edit it by hand). Keys:

- `enabled` (bool) — master on/off
- `override` (bool) — re-assert names even on tabs you renamed by hand
- `interval` (number, ≥ 10) — seconds between naming sweeps
- `template` (string) — final tab name; supports `{project}` and `{task}`,
  e.g. `{project} - {task}` (default), `{task}`, or `{project}/{task}`

Deeper knobs remain constants at the top of `tab_namer.py`: `MAX_COMMANDS`,
`MAX_TITLE_LEN`, `MAX_FULL_TITLE_LEN`.

## Testing it (manual trigger)

To watch the renamer act immediately instead of waiting for the next sweep:

```sh
iterm-tab-namer trigger
tail -n 5 ~/.config/iterm-tab-namer/tab-namer.log   # see the sweep it logged
```

This asks the running daemon to do one naming pass right now and logs a
`sweep: triggered=True …` line. It works even when `enabled` is `false`, so you
can test on demand. (A tab only gets a new name when its working dir / recent
commands / siblings actually changed since the last pass.)

## Uninstall (manual install)

```sh
launchctl bootout gui/$(id -u)/com.enjoybacon.iterm-tab-namer
rm "$HOME/Library/LaunchAgents/com.enjoybacon.iterm-tab-namer.plist"
```

(Homebrew installs uninstall with `brew uninstall iterm-tab-namer`.)

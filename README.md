# iTerm2 auto tab namer

Renames iTerm2 tabs based on their content, using Apple's on-device Foundation
Models (free, private, no API key). Tabs in the same project get distinct names —
e.g. "Auth Bug" vs "DB Migration" instead of both saying "myapp".

## Install

```sh
./install.sh
```

Then **restart iTerm2** and click **Allow** when it asks to authorize the Python API.

The installer builds the Swift binary, enables iTerm2's Python API, installs zsh
shell integration, and links the daemon into iTerm2's AutoLaunch directory.

## How it works

Every ~90s, `tab_namer.py` (an iTerm2 AutoLaunch script) reads each tab's working
directory and recent commands and asks the on-device model — via the `tabnamer`
Swift binary — for a short label. It never overrides a name you set by hand, and
only renames when a tab's content or siblings change.

Logs appear in iTerm2 → Scripts → Manage → Console.

## Settings

iTerm2 doesn't let scripts add a Preferences tab, so the live settings are
exposed through an optional **status bar component**. Add it via Settings →
Profiles → Session → Status Bar, drag in **Tab Namer**, then click *Configure
Component* to toggle **Enabled**, **Override manually-set names**, and the
**sweep interval**. The daemon runs fine with defaults if you never add it.

The remaining knobs are constants at the top of `tab_namer.py`: `MAX_COMMANDS`,
`MAX_TITLE_LEN` (and the `INTERVAL` / `OVERRIDE_MANUAL_NAMES` defaults).

## Uninstall

```sh
rm "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/tab_namer.py"
```

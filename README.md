# iTerm2 auto tab namer

Renames iTerm2 tabs based on their content, using Apple's **on-device
Foundation Models** (free, private, no API key). Tabs in the same project are
named with awareness of each other, so two tabs in the same repo working on
different problems get distinct names (e.g. "Auth Bug" vs "DB Migration").

## How it works

- **`tab_namer.py`** — an iTerm2 *AutoLaunch script* (the supported automation
  path; not a binary plugin). It connects to iTerm2's Python API, tracks each
  session's working directory and last few commands, and every ~90s builds a
  small context per tab and asks the model for a label. Tabs are grouped by git
  repo root (else cwd); siblings are passed to the model so it can differentiate.
- **`tabnamer`** — a tiny Swift binary. Reads a prompt on stdin, returns a short
  label from the on-device model. Swift is required: Foundation Models has no
  Go/C/Obj-C bindings.

### Rules

- **Never overrides a human-set name.** Once a tab's name differs from what the
  daemon last set, that tab is left alone for the rest of the session.
- **Only renames on change** — when a tab's content changes, or when its set of
  siblings changes (so it can differentiate from a newcomer).

## Configuration

Edit the constants at the top of `tab_namer.py`:

| Constant       | Default | Meaning                                  |
|----------------|---------|------------------------------------------|
| `INTERVAL`     | `90`    | Seconds between naming sweeps            |
| `MAX_COMMANDS` | `3`     | Recent commands fed to the model         |
| `MAX_TITLE_LEN`| `28`    | Hard cap on label length                 |

## Install

```sh
./install.sh
```

This builds the Swift binary, enables iTerm2's Python API, installs zsh shell
integration (for cwd + command tracking), and links the daemon into iTerm2's
AutoLaunch directory. Then **restart iTerm2** and click **Allow** when it asks to
authorize the Python API.

## Develop / test

```sh
swiftc -O tabnamer.swift -o tabnamer      # build the namer
python3 -m unittest test_tab_namer        # test the pure logic
printf 'This tab:\n  directory: myapp\n' | ./tabnamer   # smoke-test the model
```

Daemon logs appear in iTerm2 → **Scripts → Manage → Console**.

## Uninstall

```sh
rm "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/tab_namer.py"
```

(and remove the shell-integration block from `~/.zshrc` if you want).

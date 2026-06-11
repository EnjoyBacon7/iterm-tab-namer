#!/usr/bin/env bash
# Installs the iTerm2 auto tab namer (native, Rosetta-free).
#
# iTerm2's bundled Python runtime is x86_64-only, so on Apple Silicon its
# AutoLaunch scripts need Rosetta. Instead of depending on Rosetta, we run the
# daemon under a native arm64 Python venv, started by launchd, connecting to
# iTerm2 over its websocket API (the `iterm2` package fetches an auth cookie via
# AppleScript on first connect — you'll get a one-time "control iTerm2" prompt).
#
# Steps:
#   1. build the Swift `tabnamer` binary
#   2. create a native venv and install the `iterm2` package
#   3. enable iTerm2's Python API
#   4. install zsh shell integration (for cwd + command tracking)
#   5. install & load a launchd LaunchAgent that runs the daemon at login
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.13}"
VENV="$SRC_DIR/.venv"
LABEL="com.enjoybacon.iterm-tab-namer"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
SHELL_INTEGRATION="$HOME/.iterm2_shell_integration.zsh"
ZSHRC="$HOME/.zshrc"
MARKER="# >>> iterm2 shell integration (tab namer) >>>"

echo "==> Building tabnamer (Swift)…"
swiftc -O "$SRC_DIR/tabnamer.swift" -o "$SRC_DIR/tabnamer"

echo "==> Creating native venv with the iterm2 package…"
if [ ! -x "$PYTHON" ]; then
  echo "    ERROR: $PYTHON not found. Install it (e.g. 'brew install python@3.13')"
  echo "    or set PYTHON=/path/to/python3 and re-run." >&2
  exit 1
fi
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet iterm2
echo "    venv: $("$VENV/bin/python" -c 'import platform,sys;print(sys.version.split()[0], platform.machine())')"

echo "==> Enabling iTerm2 Python API…"
defaults write com.googlecode.iterm2 EnableAPIServer -bool true

echo "==> Installing zsh shell integration…"
if [ ! -f "$SHELL_INTEGRATION" ]; then
  if curl -fsSL https://iterm2.com/shell_integration/zsh -o "$SHELL_INTEGRATION"; then
    echo "    downloaded $SHELL_INTEGRATION"
  else
    echo "    WARNING: could not download shell integration (no network?)."
    echo "    Install it later via iTerm2 menu: iTerm2 > Install Shell Integration."
  fi
fi
if [ -f "$SHELL_INTEGRATION" ] && ! grep -qF "$MARKER" "$ZSHRC" 2>/dev/null; then
  {
    echo ""
    echo "$MARKER"
    echo "[ -f \"$SHELL_INTEGRATION\" ] && source \"$SHELL_INTEGRATION\""
    echo "# <<< iterm2 shell integration (tab namer) <<<"
  } >> "$ZSHRC"
  echo "    added source line to $ZSHRC"
fi

# Remove any leftover AutoLaunch script from the old (Rosetta-dependent) install.
if [ -e "$AUTOLAUNCH/tab_namer.py" ]; then
  echo "==> Removing old AutoLaunch script…"
  rm -f "$AUTOLAUNCH/tab_namer.py"
fi

echo "==> Installing launchd agent…"
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python</string>
        <string>$SRC_DIR/tab_namer.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$SRC_DIR/tabnamer.log</string>
    <key>StandardErrorPath</key>
    <string>$SRC_DIR/tabnamer.log</string>
</dict>
</plist>
PLIST_EOF

# Reload: bootout if already loaded, then bootstrap into the GUI session.
GUI="gui/$(id -u)"
launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
launchctl bootstrap "$GUI" "$PLIST"
launchctl kickstart -k "$GUI/$LABEL" 2>/dev/null || true

cat <<DONE

Done. The daemon is now running under a native arm64 Python (no Rosetta).

First-run permission:
  The first time it connects, macOS will ask to let it control iTerm2
  ("Automation" permission) — click Allow. If no prompt appears, grant it
  manually in System Settings > Privacy & Security > Automation.

Then:
  - Open some tabs, run a few commands; within ~90s they'll start naming
    themselves.
  - Logs: $SRC_DIR/tabnamer.log
  - The "Tab Namer" status bar component appears once the daemon connects:
    Settings > Profiles > Session > Configure Status Bar.

To stop it:
  launchctl bootout gui/\$(id -u)/$LABEL
  rm "$PLIST"
DONE

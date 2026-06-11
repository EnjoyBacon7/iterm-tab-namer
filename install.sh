#!/usr/bin/env bash
# Installs the iTerm2 auto tab namer:
#   1. builds the Swift `tabnamer` binary
#   2. enables iTerm2's Python API
#   3. installs zsh shell integration (for cwd + command tracking)
#   4. links the daemon into iTerm2's AutoLaunch directory
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
SHELL_INTEGRATION="$HOME/.iterm2_shell_integration.zsh"
ZSHRC="$HOME/.zshrc"
MARKER="# >>> iterm2 shell integration (tab namer) >>>"

echo "==> Building tabnamer (Swift)…"
swiftc -O "$SRC_DIR/tabnamer.swift" -o "$SRC_DIR/tabnamer"

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

echo "==> Linking daemon into AutoLaunch…"
mkdir -p "$AUTOLAUNCH"
ln -sf "$SRC_DIR/tab_namer.py" "$AUTOLAUNCH/tab_namer.py"

cat <<'DONE'

Done. Final manual steps:
  1. Restart iTerm2 (so it picks up the AutoLaunch script and shell integration).
  2. The first time the script connects, iTerm2 will ask you to authorize the
     Python API — click "Allow" (and "Always Allow" to avoid future prompts).
  3. Open some tabs, run a few commands, and within ~90s they'll start naming
     themselves. Watch progress/logs in: Scripts > Manage > Console.

To stop it: remove "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/tab_namer.py".
DONE

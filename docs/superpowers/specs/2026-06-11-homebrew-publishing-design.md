# Publish `iterm-tab-namer` to Homebrew — Design

**Date:** 2026-06-11
**Status:** Approved

## Goal

Make `iterm-tab-namer` installable via Homebrew with a clean, idiomatic
experience: `brew install` for the files, `brew services` for the login agent,
and a single `iterm-tab-namer setup` command for the environment steps Homebrew
cannot perform.

## Why a custom tap (not homebrew-core)

The tool is coupled to a GUI app (iTerm2), edits user dotfiles, writes app
defaults, and runs a login agent that drives iTerm2 over a websocket. Homebrew
core forbids all of these. A custom tap the author owns is the realistic and
correct channel. A Cask doesn't fit (no prebuilt `.app`).

## Guiding principle: let each owner own its layer

- **Homebrew owns the prefix:** building the Swift binary, vendoring the Python
  daemon and its `iterm2` dependency into an isolated venv, and managing the
  launchd login agent via a `service` block.
- **iTerm2's own mechanisms own the app config:** its Python API toggle, its
  shell-integration installer, its first-connect Automation prompt.
- **A small, explicit `setup` command owns the two scriptable environment
  steps** — nothing mutates the user's environment silently or behind a daemon.

The macOS Automation permission is a TCC click that cannot be scripted by
anyone, so a literal "one command, done" was never possible. This design is as
close to that as the platform allows while staying clean.

## Repositories

### Project repo — `EnjoyBacon7/iterm-tab-namer`
- Add the `iterm-tab-namer setup` subcommand (see below).
- Add a `LICENSE` file (MIT).
- Keep `install.sh` as a non-Homebrew install path; add a README note pointing
  to the Homebrew route as the recommended option.
- Cut a tagged release `v0.1.0` so the formula has a stable source tarball and
  `sha256`.

### Tap repo — `EnjoyBacon7/homebrew-tap` (new)
- Created via `gh` during implementation.
- Must be named `homebrew-<name>` so the short install syntax works:
  `brew install EnjoyBacon7/tap/iterm-tab-namer`.
- Contains `Formula/iterm-tab-namer.rb`.

## Formula design (`Formula/iterm-tab-namer.rb`)

```ruby
class ItermTabNamer < Formula
  include Language::Python::Virtualenv
  desc     "Auto-rename iTerm2 tabs from their content using Apple's on-device model"
  homepage "https://github.com/EnjoyBacon7/iterm-tab-namer"
  url      "https://github.com/EnjoyBacon7/iterm-tab-namer/archive/refs/tags/v0.1.0.tar.gz"
  sha256   "<filled at release>"
  license  "MIT"

  depends_on :macos
  depends_on macos: :tahoe   # FoundationModels requires macOS 26+
  depends_on arch: :arm64    # on-device model is Apple Silicon only
  depends_on "python@3.13"
  # Swift toolchain requirement (CLT vs full Xcode) verified during impl;
  # add `depends_on xcode: :build` only if CLT proves insufficient.

  # iterm2 + transitive deps as `resource` blocks,
  # generated with `brew update-python-resources`.

  def install
    system "swiftc", "-O", "tabnamer.swift", "-o", "tabnamer"
    libexec.install "tabnamer", "tab_namer.py"   # co-located → daemon finds the binary unchanged
    venv = virtualenv_create(libexec/"venv", "python3.13")
    venv.pip_install resources
    (bin/"iterm-tab-namer").write <<~SH
      #!/bin/bash
      exec "#{libexec}/venv/bin/python" "#{libexec}/tab_namer.py" "$@"
    SH
  end

  service do
    run [opt_bin/"iterm-tab-namer"]
    keep_alive true
    log_path       var/"log/iterm-tab-namer.log"
    error_log_path var/"log/iterm-tab-namer.log"
  end

  def caveats
    <<~EOS
      Finish setup (one time):
        iterm-tab-namer setup
      Start it at login:
        brew services start iterm-tab-namer
      Restart iTerm2, then click Allow when macOS asks to control iTerm2.
    EOS
  end

  test do
    assert_path_exists libexec/"tabnamer"
  end
end
```

### Why no binary-path code change is needed
The daemon resolves `NAMER_BIN = dirname(__file__)/tabnamer`. Because the
formula installs `tabnamer` and `tab_namer.py` into the **same `libexec`
directory**, that resolution still points at the binary. No change to the
binary-lookup logic.

### Why `virtualenv_create` + `pip_install resources` (not a pip package)
The project is a lean single-file daemon, not a pip-installable package.
`virtualenv_create` builds an isolated venv in the Cellar and `pip_install
resources` vendors `iterm2` (and its transitive deps) into it — no need to
restructure the repo into a packaged Python distribution. A thin shell wrapper
in `bin` execs the venv Python against the daemon script.

## Project change: `iterm-tab-namer setup`

`tab_namer.py`'s `__main__` currently runs the daemon immediately. Add argv
dispatch:

- **no args** → run the daemon (unchanged; this is what `brew services` calls).
- **`setup`** → the scriptable environment steps ported from `install.sh`:
  1. `defaults write com.googlecode.iterm2 EnableAPIServer -bool true`
  2. download iTerm2 shell integration to `~/.iterm2_shell_integration.zsh` if
     missing (graceful if offline)
  3. idempotently append the marker-guarded `source` line to `~/.zshrc`
- **`setup --undo`** → reverse the above (remove the marker-guarded `.zshrc`
  block) so teardown is clean, since `brew uninstall` cannot touch dotfiles.

The daemon-launch, venv-creation, Swift build, and launchd steps that
`install.sh` performs are **not** part of `setup` — Homebrew owns those now.

The `iterm2` import in `tab_namer.py` is already guarded, so the `setup` path
runs without the package present at import time; argv dispatch must occur before
any `iterm2.run_forever` call.

## Lifecycle

| Action          | Command                                            |
|-----------------|----------------------------------------------------|
| Install         | `brew install EnjoyBacon7/tap/iterm-tab-namer`     |
| Configure env   | `iterm-tab-namer setup` (once) + click Allow       |
| Run at login    | `brew services start iterm-tab-namer`              |
| Stop            | `brew services stop iterm-tab-namer`               |
| Remove          | `iterm-tab-namer setup --undo` then `brew uninstall iterm-tab-namer` |

## Validation before publishing

- `brew install --build-from-source EnjoyBacon7/tap/iterm-tab-namer`
- `brew test iterm-tab-namer`
- `brew audit --new --strict --online iterm-tab-namer`
- Existing unit tests (`test_tab_namer.py`) still pass, including any covering
  the new argv dispatch.

## Out of scope

- Submitting to homebrew-core.
- Building/shipping bottles (prebuilt binaries); build-from-source is acceptable
  for a tap at this stage.
- Removing or rewriting `install.sh`.

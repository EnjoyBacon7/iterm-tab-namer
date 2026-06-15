# Publish `iterm-tab-namer` to Homebrew — Design

**Date:** 2026-06-11
**Status:** Approved

## Goal

Make `iterm-tab-namer` installable via Homebrew with a clean, idiomatic
experience: `brew install` for the files, `brew services` for the login agent,
and a short list of one-time steps the user performs through iTerm2's own GUI
for the environment changes Homebrew cannot (and should not) perform.

> **Update 2026-06-15:** The original design added an `iterm-tab-namer setup`
> subcommand that ran `defaults write`, fetched shell integration over the
> network, and edited `~/.zshrc`. We dropped it. Those side effects all live
> outside Homebrew's prefix and are already doable through iTerm2's GUI
> (Settings → enable Python API; iTerm2 menu → Install Shell Integration, which
> edits the shell rc itself). The formula now just ships the daemon and prints
> these manual steps in `caveats`. The daemon binary takes no subcommands.

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
  shell-integration installer (which edits the shell rc itself), its
  first-connect Automation prompt. The user performs these through iTerm2's GUI.

Nothing in the formula mutates the user's environment: no dotfile edits, no
`defaults write`, no runtime network fetch. The environment steps are listed in
`caveats` for the user to do via iTerm2. The macOS Automation permission is a
TCC click that cannot be scripted by anyone, so a literal "one command, done"
was never possible anyway; keeping the formula prefix-clean is the right trade.

## Repositories

### Project repo — `EnjoyBacon7/iterm-tab-namer`
- No daemon code change is needed: it already runs with no args and resolves the
  helper binary relative to its own path.
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
      One-time setup in iTerm2:
        1. Settings > General > Magic > enable "Python API"
        2. iTerm2 menu > Install Shell Integration
        3. Restart iTerm2, then click Allow when it asks to control iTerm2.
      Start it at login:
        brew services start iterm-tab-namer
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

## Project change: none in the daemon

`tab_namer.py`'s `__main__` runs the daemon with no args, which is exactly what
`brew services` invokes — so no code change is required. The environment steps
that `install.sh` automates are instead listed in the formula `caveats` for the
user to perform through iTerm2's GUI:

1. **Enable the Python API:** Settings → General → Magic → "Python API".
2. **Install shell integration:** iTerm2 menu → Install Shell Integration. This
   downloads the integration and edits the shell rc itself — iTerm2 owns its own
   integration, so we don't touch `~/.zshrc`.
3. **Restart iTerm2 and click Allow** at the first-connect Automation prompt.

Because nothing edits dotfiles or app defaults, `brew uninstall` is a clean
teardown on its own; there is no `--undo` step to run.

## Lifecycle

| Action          | Command / step                                     |
|-----------------|----------------------------------------------------|
| Install         | `brew install EnjoyBacon7/tap/iterm-tab-namer`     |
| Configure env   | iTerm2 GUI: enable Python API + Install Shell Integration, restart, click Allow |
| Run at login    | `brew services start iterm-tab-namer`              |
| Stop            | `brew services stop iterm-tab-namer`               |
| Remove          | `brew uninstall iterm-tab-namer`                   |

## Validation before publishing

- `brew install --build-from-source EnjoyBacon7/tap/iterm-tab-namer`
- `brew test iterm-tab-namer`
- `brew audit --new --strict --online iterm-tab-namer`
- Existing unit tests (`test_tab_namer.py`) still pass.

## Out of scope

- Submitting to homebrew-core.
- Building/shipping bottles (prebuilt binaries); build-from-source is acceptable
  for a tap at this stage.
- Removing or rewriting `install.sh`.

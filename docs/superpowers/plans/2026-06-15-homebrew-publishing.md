# Homebrew Publishing Implementation Plan

> **Superseded 2026-06-15:** Tasks 1–4 (the `iterm-tab-namer setup` subcommand,
> `.zshrc` editing, shell-integration download, API-toggle, and argv dispatch)
> were implemented and then **removed** by a later decision: the formula no
> longer mutates the user's environment at all. Those one-time steps are now
> done by the user through iTerm2's GUI and listed in the formula `caveats`. The
> daemon takes no subcommands. Tasks 5–9 (LICENSE, README, formula, tap,
> release, validation) still apply. See the updated design spec for the current
> approach: `docs/superpowers/specs/2026-06-11-homebrew-publishing-design.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `iterm-tab-namer` installable the standard Homebrew way (`brew install`, `brew services`, one `iterm-tab-namer setup` command), retiring the bespoke `install.sh`/plist as the primary path.

**Architecture:** Add `argv` dispatch to `tab_namer.py` so `tab_namer.py setup` performs the scriptable environment steps (iTerm2 API toggle, shell-integration download, marker-guarded `.zshrc` edit) and `tab_namer.py setup --undo` reverses the dotfile edit; no args still runs the daemon. A custom Homebrew tap formula builds the Swift binary, vendors the Python daemon + `iterm2` into an isolated venv, and runs it under `brew services`. The dotfile/setup logic is factored into pure, file-path-taking functions that are unit-tested without iTerm2 or network.

**Tech Stack:** Python 3.13 (`unittest`), Ruby (Homebrew formula), `gh` CLI, `brew`, Swift (`swiftc`).

**Spec:** `docs/superpowers/specs/2026-06-11-homebrew-publishing-design.md`

---

## File Structure

- `tab_namer.py` — **modify**: add `import sys`, `import subprocess`; marker constants; pure `shell_integration_lines`, `ensure_shell_integration`, `remove_shell_integration`; side-effecting `enable_iterm_api`, `download_shell_integration`, `cmd_setup`; replace the `__main__` guard with `_cli(argv)` dispatch.
- `test_tab_namer.py` — **modify**: unit tests for the three pure dotfile functions (tmp-file based) and `_cli` dispatch (monkeypatched).
- `LICENSE` — **create**: MIT license text.
- `README.md` — **modify**: add Homebrew as the recommended install path; keep `install.sh` documented as the manual alternative.
- `Formula/iterm-tab-namer.rb` — **create here for review, then publish to the tap repo** (the formula does not live in this repo long-term; it ships in `EnjoyBacon7/homebrew-tap`). Created under a gitignored `dist/` staging dir so it is reviewable without polluting the project repo.

**TDD boundary:** Tasks 1–4 are pure/dispatch logic with unit tests. Tasks 5–9 (LICENSE, README, formula authoring, tap creation, release tagging, `brew` validation) are content/external-action tasks — they have verification commands but not `unittest` cases, because they touch GitHub, the network, and the Homebrew prefix. Each such step states its exact command and expected output.

---

## Task 1: Pure shell-integration block builder

**Files:**
- Modify: `tab_namer.py` (add near the other module constants, after `_HOME` around line 72)
- Test: `test_tab_namer.py`

- [ ] **Step 1: Write the failing test**

Add to `test_tab_namer.py`:

```python
class TestShellIntegrationLines(unittest.TestCase):
    def test_contains_markers_and_source(self):
        block = tn.shell_integration_lines("/Users/me/.iterm2_shell_integration.zsh")
        self.assertIn(tn.SHELL_INTEGRATION_MARKER_START, block)
        self.assertIn(tn.SHELL_INTEGRATION_MARKER_END, block)
        self.assertIn(
            'source "/Users/me/.iterm2_shell_integration.zsh"', block
        )

    def test_starts_and_ends_with_newline(self):
        block = tn.shell_integration_lines("/x/.iterm2_shell_integration.zsh")
        self.assertTrue(block.startswith("\n"))
        self.assertTrue(block.endswith("\n"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_tab_namer.TestShellIntegrationLines -v`
Expected: FAIL with `AttributeError: module 'tab_namer' has no attribute 'SHELL_INTEGRATION_MARKER_START'`

- [ ] **Step 3: Write the implementation**

Add to `tab_namer.py` after the `_HOME = os.path.expanduser("~")` line:

```python
# Marker-guarded block appended to ~/.zshrc by `setup` (matches install.sh so an
# existing install is recognized and not duplicated). `setup --undo` removes it.
SHELL_INTEGRATION_MARKER_START = "# >>> iterm2 shell integration (tab namer) >>>"
SHELL_INTEGRATION_MARKER_END = "# <<< iterm2 shell integration (tab namer) <<<"
SHELL_INTEGRATION_URL = "https://iterm2.com/shell_integration/zsh"


def shell_integration_lines(integration_path):
    """The marker-guarded zsh block that sources iTerm2 shell integration."""
    return (
        "\n"
        + SHELL_INTEGRATION_MARKER_START + "\n"
        + f'[ -f "{integration_path}" ] && source "{integration_path}"' + "\n"
        + SHELL_INTEGRATION_MARKER_END + "\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_tab_namer.TestShellIntegrationLines -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add tab_namer.py test_tab_namer.py
git commit -m "Add marker constants and shell-integration block builder"
```

---

## Task 2: Idempotent append / remove of the .zshrc block

**Files:**
- Modify: `tab_namer.py` (after `shell_integration_lines`)
- Test: `test_tab_namer.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_tab_namer.py` (add `import tempfile` at the top of the file if not present):

```python
class TestZshrcBlock(unittest.TestCase):
    def _tmp(self, contents=""):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(contents)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_ensure_appends_when_absent(self):
        zshrc = self._tmp("export PATH=/usr/bin\n")
        added = tn.ensure_shell_integration(zshrc, "/i/.zsh")
        self.assertTrue(added)
        with open(zshrc) as f:
            body = f.read()
        self.assertIn(tn.SHELL_INTEGRATION_MARKER_START, body)
        self.assertIn("export PATH=/usr/bin", body)  # preserved

    def test_ensure_is_idempotent(self):
        zshrc = self._tmp("export PATH=/usr/bin\n")
        tn.ensure_shell_integration(zshrc, "/i/.zsh")
        added_again = tn.ensure_shell_integration(zshrc, "/i/.zsh")
        self.assertFalse(added_again)
        with open(zshrc) as f:
            self.assertEqual(f.read().count(tn.SHELL_INTEGRATION_MARKER_START), 1)

    def test_ensure_creates_file_when_missing(self):
        path = self._tmp()
        os.remove(path)  # ensure it does not exist
        added = tn.ensure_shell_integration(path, "/i/.zsh")
        self.assertTrue(added)
        self.assertTrue(os.path.exists(path))

    def test_remove_strips_block_and_keeps_rest(self):
        zshrc = self._tmp("line A\n")
        tn.ensure_shell_integration(zshrc, "/i/.zsh")
        removed = tn.remove_shell_integration(zshrc)
        self.assertTrue(removed)
        with open(zshrc) as f:
            body = f.read()
        self.assertNotIn(tn.SHELL_INTEGRATION_MARKER_START, body)
        self.assertNotIn(tn.SHELL_INTEGRATION_MARKER_END, body)
        self.assertIn("line A", body)

    def test_remove_returns_false_when_absent(self):
        zshrc = self._tmp("nothing here\n")
        self.assertFalse(tn.remove_shell_integration(zshrc))

    def test_remove_noop_when_file_missing(self):
        path = self._tmp()
        os.remove(path)
        self.assertFalse(tn.remove_shell_integration(path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_tab_namer.TestZshrcBlock -v`
Expected: FAIL with `AttributeError: module 'tab_namer' has no attribute 'ensure_shell_integration'`

- [ ] **Step 3: Write the implementation**

Add to `tab_namer.py` after `shell_integration_lines`:

```python
def ensure_shell_integration(zshrc_path, integration_path):
    """Append the marker-guarded block to zshrc if not already present.

    Returns True if the block was added, False if it was already there.
    Creates the file if it does not exist.
    """
    existing = ""
    if os.path.exists(zshrc_path):
        with open(zshrc_path, "r", encoding="utf-8") as f:
            existing = f.read()
    if SHELL_INTEGRATION_MARKER_START in existing:
        return False
    with open(zshrc_path, "a", encoding="utf-8") as f:
        f.write(shell_integration_lines(integration_path))
    return True


def remove_shell_integration(zshrc_path):
    """Remove the marker-guarded block from zshrc.

    Returns True if a block was removed, False if none was present (or no file).
    """
    if not os.path.exists(zshrc_path):
        return False
    with open(zshrc_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    start = end = None
    for i, line in enumerate(lines):
        if line.strip() == SHELL_INTEGRATION_MARKER_START:
            start = i
        elif line.strip() == SHELL_INTEGRATION_MARKER_END:
            end = i
            break
    if start is None or end is None or end < start:
        return False
    # Also drop a single blank separator line immediately before the block.
    drop_from = start
    if drop_from > 0 and lines[drop_from - 1].strip() == "":
        drop_from -= 1
    del lines[drop_from:end + 1]
    with open(zshrc_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_tab_namer.TestZshrcBlock -v`
Expected: PASS (6)

- [ ] **Step 5: Commit**

```bash
git add tab_namer.py test_tab_namer.py
git commit -m "Idempotently append and remove the zshrc integration block"
```

---

## Task 3: Side-effecting setup steps (API toggle, download)

**Files:**
- Modify: `tab_namer.py` (add `import subprocess` near the top imports; add functions after `remove_shell_integration`)

These two functions shell out (`defaults`, `curl`) and are not unit-tested
(they touch system defaults and the network); they are thin wrappers verified by
the end-to-end run in Task 9. Keep them small so all real logic stays in the
tested functions from Tasks 1–2.

- [ ] **Step 1: Add the subprocess import**

In the import block (around line 22-25, after `import re`) add:

```python
import subprocess
import sys
```

- [ ] **Step 2: Add the side-effecting helpers**

Add to `tab_namer.py` after `remove_shell_integration`:

```python
def enable_iterm_api():
    """Turn on iTerm2's Python API server (idempotent)."""
    subprocess.run(
        ["defaults", "write", "com.googlecode.iterm2", "EnableAPIServer",
         "-bool", "true"],
        check=False,
    )


def download_shell_integration(integration_path):
    """Download iTerm2 zsh shell integration if absent. Returns True on success
    or if already present; False if the download failed (offline)."""
    if os.path.exists(integration_path):
        return True
    result = subprocess.run(
        ["curl", "-fsSL", SHELL_INTEGRATION_URL, "-o", integration_path],
        check=False,
    )
    return result.returncode == 0
```

- [ ] **Step 3: Verify the module still imports**

Run: `python3 -c "import tab_namer; print(callable(tab_namer.enable_iterm_api), callable(tab_namer.download_shell_integration))"`
Expected: prints `True True`

- [ ] **Step 4: Run the full suite (no regressions)**

Run: `python3 -m unittest test_tab_namer`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add tab_namer.py
git commit -m "Add setup side-effect helpers for API toggle and integration download"
```

---

## Task 4: `setup` command + argv dispatch

**Files:**
- Modify: `tab_namer.py` (add `cmd_setup` after `download_shell_integration`; replace the `__main__` guard at the end of the file, lines 676-677)
- Test: `test_tab_namer.py`

- [ ] **Step 1: Write the failing tests for dispatch**

Add to `test_tab_namer.py`:

```python
class TestCli(unittest.TestCase):
    def test_setup_dispatches_to_cmd_setup(self):
        calls = []
        orig = tn.cmd_setup
        tn.cmd_setup = lambda undo=False: calls.append(undo) or 0
        try:
            rc = tn._cli(["setup"])
        finally:
            tn.cmd_setup = orig
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [False])

    def test_setup_undo_passes_flag(self):
        calls = []
        orig = tn.cmd_setup
        tn.cmd_setup = lambda undo=False: calls.append(undo) or 0
        try:
            tn._cli(["setup", "--undo"])
        finally:
            tn.cmd_setup = orig
        self.assertEqual(calls, [True])

    def test_no_args_without_iterm2_errors_cleanly(self):
        # When the iterm2 package is unavailable, running the daemon returns
        # nonzero rather than raising.
        if tn.iterm2 is not None:
            self.skipTest("iterm2 present in this environment")
        rc = tn._cli([])
        self.assertEqual(rc, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_tab_namer.TestCli -v`
Expected: FAIL with `AttributeError: module 'tab_namer' has no attribute 'cmd_setup'` (or `_cli`)

- [ ] **Step 3: Implement `cmd_setup` and `_cli`**

Add to `tab_namer.py` after `download_shell_integration`:

```python
def cmd_setup(undo=False):
    """Perform (or reverse) the scriptable environment steps Homebrew can't do.

    Forward: enable iTerm2's API, download shell integration if missing, and add
    the marker-guarded source line to ~/.zshrc.
    Undo: remove the marker-guarded block from ~/.zshrc.
    Returns a process exit code (0 on success).
    """
    zshrc = os.path.join(_HOME, ".zshrc")
    integration = os.path.join(_HOME, ".iterm2_shell_integration.zsh")
    if undo:
        removed = remove_shell_integration(zshrc)
        print("Removed shell-integration block from ~/.zshrc"
              if removed else "No shell-integration block found in ~/.zshrc")
        print("Note: iTerm2's API toggle and the downloaded integration file "
              "are left in place; remove them manually if desired.")
        return 0
    enable_iterm_api()
    print("Enabled iTerm2 Python API.")
    if download_shell_integration(integration):
        print(f"Shell integration ready at {integration}")
    else:
        print("WARNING: could not download shell integration (offline?). "
              "Install it later via iTerm2 > Install Shell Integration.")
    if ensure_shell_integration(zshrc, integration):
        print("Added shell-integration source line to ~/.zshrc "
              "(restart your shell).")
    else:
        print("~/.zshrc already sources the shell integration.")
    print("\nNext: `brew services start iterm-tab-namer`, restart iTerm2, and "
          "click Allow when macOS asks to control iTerm2.")
    return 0


def _cli(argv):
    """Entry point. `setup`/`setup --undo` run the environment steps; no args
    runs the daemon (requires the iterm2 package)."""
    if argv and argv[0] == "setup":
        return cmd_setup(undo="--undo" in argv[1:])
    if iterm2 is None:
        print("the 'iterm2' package is not available; cannot run the daemon",
              file=sys.stderr)
        return 1
    iterm2.run_forever(main)
    return 0
```

Replace the file's final block (lines 676-677):

```python
if __name__ == "__main__" and iterm2 is not None:
    iterm2.run_forever(main)
```

with:

```python
if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_tab_namer.TestCli -v`
Expected: PASS (3; the third may report `skipped` if `iterm2` is importable)

- [ ] **Step 5: Smoke-test setup against a throwaway HOME**

Run:

```bash
TMPHOME=$(mktemp -d); HOME="$TMPHOME" python3 -c "import tab_namer as t; t.ensure_shell_integration('$TMPHOME/.zshrc', '$TMPHOME/.iterm2_shell_integration.zsh')"; grep -c "tab namer" "$TMPHOME/.zshrc"; rm -rf "$TMPHOME"
```

Expected: prints `1` (block added exactly once). (This avoids running the real `defaults`/`curl` in `cmd_setup`.)

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest test_tab_namer`
Expected: OK

- [ ] **Step 7: Commit**

```bash
git add tab_namer.py test_tab_namer.py
git commit -m "Add 'setup' subcommand and argv dispatch"
```

---

## Task 5: MIT LICENSE

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Create the LICENSE file**

Create `LICENSE` with the MIT text (replace nothing — this is the exact content):

```
MIT License

Copyright (c) 2026 EnjoyBacon7

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Verify**

Run: `head -1 LICENSE`
Expected: `MIT License`

- [ ] **Step 3: Commit**

```bash
git add LICENSE
git commit -m "Add MIT license"
```

---

## Task 6: README — Homebrew as the recommended path

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current install section**

Run: `grep -n "install" README.md | head`
Expected: shows the section that documents `install.sh`.

- [ ] **Step 2: Add a Homebrew section above the install.sh instructions**

Insert this block immediately before the existing manual-install instructions
in `README.md` (keep the `install.sh` content as the "Manual install"
alternative):

```markdown
## Install (Homebrew — recommended)

```bash
brew install EnjoyBacon7/tap/iterm-tab-namer
iterm-tab-namer setup            # one-time: enables iTerm2's API, shell integration, ~/.zshrc
brew services start iterm-tab-namer
```

Restart iTerm2, then click **Allow** when macOS asks to control iTerm2.

To stop or remove:

```bash
brew services stop iterm-tab-namer
iterm-tab-namer setup --undo     # removes the ~/.zshrc block
brew uninstall iterm-tab-namer
```

Requires macOS 26+ on Apple Silicon (the on-device model is arm64-only).

## Manual install (alternative)
```

- [ ] **Step 3: Verify the section renders**

Run: `grep -n "Homebrew — recommended" README.md`
Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document Homebrew as the recommended install path"
```

---

## Task 7: Author the formula (staged in dist/, gitignored)

**Files:**
- Modify: `.gitignore` (add `/dist/`)
- Create: `dist/Formula/iterm-tab-namer.rb`

The formula's final home is the tap repo (Task 8). We stage it under a
gitignored `dist/` so it is reviewable here without committing tap content into
the project repo.

- [ ] **Step 1: Gitignore the staging dir**

Append to `.gitignore`:

```
/dist/
```

- [ ] **Step 2: Write the formula**

Create `dist/Formula/iterm-tab-namer.rb`:

```ruby
class ItermTabNamer < Formula
  include Language::Python::Virtualenv

  desc     "Auto-rename iTerm2 tabs from their content using Apple's on-device model"
  homepage "https://github.com/EnjoyBacon7/iterm-tab-namer"
  url      "https://github.com/EnjoyBacon7/iterm-tab-namer/archive/refs/tags/v0.1.0.tar.gz"
  sha256   "REPLACE_WITH_RELEASE_TARBALL_SHA256"
  license  "MIT"

  depends_on arch: :arm64        # on-device model is Apple Silicon only
  depends_on macos: :tahoe       # FoundationModels requires macOS 26+
  depends_on "python@3.13"

  # iterm2 + transitive deps. Generated with `brew update-python-resources`
  # in Task 8, Step 4 — do not hand-write these.

  def install
    system "swiftc", "-O", "tabnamer.swift", "-o", "tabnamer"
    libexec.install "tabnamer", "tab_namer.py"
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

- [ ] **Step 3: Verify Ruby syntax**

Run: `ruby -c dist/Formula/iterm-tab-namer.rb`
Expected: `Syntax OK`

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "Stage Homebrew formula under gitignored dist/"
```

(The formula file itself is gitignored; that is intentional — it ships to the tap repo in Task 8.)

---

## Task 8: Cut release, create tap, finalize formula (external actions)

**Files:** none in this repo (operates on GitHub + a new tap repo).

> These steps require `gh` authenticated as the repo owner and a working `brew`.
> They are manual/external and have no unit tests; verify via the stated output.
> Run them only after Tasks 1–7 are merged to `main`.

- [ ] **Step 1: Tag and push the release**

Run:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Expected: the tag appears at `https://github.com/EnjoyBacon7/iterm-tab-namer/releases`.

- [ ] **Step 2: Compute the tarball sha256**

Run:

```bash
curl -fsSL https://github.com/EnjoyBacon7/iterm-tab-namer/archive/refs/tags/v0.1.0.tar.gz -o /tmp/itn.tgz
shasum -a 256 /tmp/itn.tgz
```

Expected: a 64-hex-char digest. Put it in the formula's `sha256` field, replacing `REPLACE_WITH_RELEASE_TARBALL_SHA256`.

- [ ] **Step 3: Create the tap repo**

Run:

```bash
gh repo create EnjoyBacon7/homebrew-tap --public --description "Homebrew tap for iterm-tab-namer"
git clone https://github.com/EnjoyBacon7/homebrew-tap /tmp/homebrew-tap
mkdir -p /tmp/homebrew-tap/Formula
cp dist/Formula/iterm-tab-namer.rb /tmp/homebrew-tap/Formula/
```

Expected: the repo is created and the formula is copied in.

- [ ] **Step 4: Generate the Python resource stanzas**

Run:

```bash
cd /tmp/homebrew-tap
brew update-python-resources Formula/iterm-tab-namer.rb
```

Expected: `resource "iterm2" do … end` (and transitive deps) are injected into the formula. Re-run `ruby -c Formula/iterm-tab-namer.rb` → `Syntax OK`.

- [ ] **Step 5: Commit and push the tap**

Run:

```bash
cd /tmp/homebrew-tap
git add Formula/iterm-tab-namer.rb
git commit -m "Add iterm-tab-namer formula v0.1.0"
git push origin HEAD
```

Expected: formula is live in the tap.

---

## Task 9: Validate the Homebrew install end-to-end (external)

**Files:** none.

> Requires macOS 26+ on Apple Silicon with the on-device model available.

- [ ] **Step 1: Build-from-source install**

Run: `brew install --build-from-source EnjoyBacon7/tap/iterm-tab-namer`
Expected: builds the Swift binary, creates the venv, installs `iterm2`, links `bin/iterm-tab-namer`. No errors.

- [ ] **Step 2: Formula self-test and audit**

Run:

```bash
brew test iterm-tab-namer
brew audit --new --strict --online iterm-tab-namer
```

Expected: test passes (the binary exists); audit reports no errors.

- [ ] **Step 3: Run setup and start the service**

Run:

```bash
iterm-tab-namer setup
brew services start iterm-tab-namer
```

Expected: setup prints the API/integration/zshrc messages; `brew services list` shows `iterm-tab-namer` as `started`.

- [ ] **Step 4: Confirm naming works and only one helper runs**

Open a few iTerm2 tabs, run commands, wait ~90s.
Expected: tabs name themselves `project - Task`; `pgrep -fl tabnamer` shows at most one resident helper (carried over from the resident-helper work).

- [ ] **Step 5: Confirm clean teardown**

Run:

```bash
brew services stop iterm-tab-namer
iterm-tab-namer setup --undo
brew uninstall iterm-tab-namer
```

Expected: service stops; the `~/.zshrc` block is removed; the formula uninstalls cleanly.

- [ ] **Step 6: Final unit-test run in the project repo**

Run: `python3 -m unittest test_tab_namer`
Expected: OK (all tests, including the new setup/dispatch tests).

---

## Self-Review Notes

- **Spec coverage:**
  - `iterm-tab-namer setup` subcommand (spec §"Project change") → Tasks 1–4.
  - `setup --undo` reverses the dotfile edit → Task 2 (`remove_shell_integration`) + Task 4 (`cmd_setup(undo=True)`).
  - no-args runs daemon; argv dispatch before `run_forever`; `iterm2` import stays guarded → Task 4 (`_cli`).
  - LICENSE (MIT) → Task 5.
  - README note pointing to Homebrew route → Task 6 (and keeps `install.sh` documented, per "Out of scope: not removing install.sh").
  - Formula (build Swift, co-locate binary+script, venv + `pip_install resources`, `service` block, `caveats`, `test`) → Task 7, with resources generated in Task 8 Step 4.
  - Custom tap `EnjoyBacon7/homebrew-tap`, named `homebrew-tap` for short syntax → Task 8.
  - Tagged release `v0.1.0` + `sha256` → Task 8 Steps 1–2.
  - Validation (`--build-from-source`, `brew test`, `brew audit`, unit tests) → Task 9.
  - Lifecycle table commands → exercised across Tasks 6 and 9.
- **Out-of-scope respected:** no homebrew-core submission, no bottles, `install.sh` retained.
- **Name/type consistency:** `shell_integration_lines`, `ensure_shell_integration`, `remove_shell_integration`, `enable_iterm_api`, `download_shell_integration`, `cmd_setup(undo=False)`, `_cli(argv)`, `SHELL_INTEGRATION_MARKER_START/END`, `SHELL_INTEGRATION_URL` are used consistently across Tasks 1–4 and the tests.
- **No placeholders** in repo code/tests. The single intentional sentinel `REPLACE_WITH_RELEASE_TARBALL_SHA256` lives only in the staged formula and is filled in Task 8 Step 2 — it cannot be known before the release tarball exists.

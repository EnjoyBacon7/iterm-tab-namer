# Resident Naming Helper + Templatable Tab Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Apple's on-device Foundation Model loaded once across all naming requests (ending the per-call model-load power drain), and let users shape tab titles with a `{project}`/`{task}` template defaulting to `{project} - {task}`.

**Architecture:** `tabnamer.swift` becomes a persistent length-prefixed request/response loop that loads the model once. `tab_namer.py` gains a helper-manager that owns the resident subprocess and serializes framed requests, plus a pure `render_title()` that composes the final name from a configurable template; the model is retuned to emit only the distinguishing `{task}`.

**Tech Stack:** Python 3.13 (asyncio, `iterm2` package), Swift (`FoundationModels`), `unittest` (`test_tab_namer.py`).

---

## File Structure

- `tabnamer.swift` — **modify**: wrap the existing single-shot inference in a read-eval loop with length-prefixed framing over stdin/stdout; prewarm the model at startup.
- `tab_namer.py` — **modify**: add framing helpers + a `NamerProcess` manager class; rewrite `run_namer` to use the resident process; add `TITLE_TEMPLATE`, `CONFIG["template"]`, `KNOB_TEMPLATE`, the status-bar `StringKnob`; add pure `render_title`; retune `INSTRUCTIONS`; update `maybe_rename` to compose via `render_title`.
- `test_tab_namer.py` — **modify**: add tests for `render_title` and the request/response framing helpers (all iTerm2-free).
- `install.sh` — **unchanged** (same `swiftc -O` build, same launchd setup).

Note on TDD scope: `render_title` and the framing helpers are pure and unit-tested first. The Swift loop and the `NamerProcess` asyncio glue are integration-level and verified by a manual end-to-end check (Task 7), since they require iTerm2 + the on-device model.

---

## Task 1: Pure `render_title` composition

**Files:**
- Modify: `tab_namer.py` (add `render_title` near `directory_label`, around line 240)
- Test: `test_tab_namer.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_tab_namer.py` (import `render_title` alongside the existing imports from `tab_namer`):

```python
def test_render_title_both_filled(self):
    self.assertEqual(
        tab_namer.render_title("{project} - {task}", "payments-api", "Webhook Retry Fix"),
        "payments-api - Webhook Retry Fix",
    )

def test_render_title_empty_task_collapses_separator(self):
    self.assertEqual(
        tab_namer.render_title("{project} - {task}", "payments-api", ""),
        "payments-api",
    )

def test_render_title_empty_project_collapses_to_task(self):
    self.assertEqual(
        tab_namer.render_title("{project} - {task}", "", "Webhook Retry Fix"),
        "Webhook Retry Fix",
    )

def test_render_title_both_empty(self):
    self.assertEqual(tab_namer.render_title("{project} - {task}", "", ""), "")

def test_render_title_collapses_whitespace(self):
    self.assertEqual(
        tab_namer.render_title("{project}  -  {task}", "repo", "Do  Thing"),
        "repo - Do Thing",
    )

def test_render_title_caps_final_length(self):
    long_proj = "averylongrepositoryname"
    out = tab_namer.render_title("{project} - {task}", long_proj, "Some Task Here")
    self.assertLessEqual(len(out), tab_namer.MAX_TITLE_LEN)

def test_render_title_custom_template(self):
    self.assertEqual(
        tab_namer.render_title("{project}/{task}", "repo", "Fix Bug"),
        "repo/Fix Bug",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_tab_namer.py -k render_title -v` (or `python3 -m unittest test_tab_namer -v`)
Expected: FAIL with `AttributeError: module 'tab_namer' has no attribute 'render_title'`

- [ ] **Step 3: Write the implementation**

Add to `tab_namer.py` after `directory_label`:

```python
def render_title(template, project, task):
    """Compose the final tab name from a template and its parts.

    Substitutes {project} and {task}. When a value is empty, its placeholder is
    removed along with an adjoining separator so no dangling separators remain
    (e.g. "repo - " collapses to "repo"). Whitespace is collapsed and the result
    is capped at MAX_TITLE_LEN.
    """
    out = template
    # Remove an empty placeholder together with one adjacent separator run.
    for token, value in (("{project}", project or ""), ("{task}", task or "")):
        if value:
            out = out.replace(token, value)
        else:
            out = re.sub(
                r"\s*[-–—:/|]?\s*" + re.escape(token) + r"\s*[-–—:/|]?\s*",
                " ",
                out,
            )
    out = " ".join(out.split())
    out = out.strip(" -–—:/|")
    out = " ".join(out.split())
    if len(out) > MAX_TITLE_LEN:
        out = out[:MAX_TITLE_LEN].rstrip()
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_tab_namer.py -k render_title -v`
Expected: PASS (all 7)

- [ ] **Step 5: Commit**

```bash
git add tab_namer.py test_tab_namer.py
git commit -m "Add pure render_title for templatable tab names"
```

---

## Task 2: Template configuration (constant, CONFIG, knob)

**Files:**
- Modify: `tab_namer.py` (constants near line 33; `CONFIG` near line 78; `KNOB_*` near line 84; `register_status_bar` near line 507)

- [ ] **Step 1: Add the constant**

In the configuration block (after `NAMER_TIMEOUT`, around line 37) add:

```python
# Template for the final tab name. Supported variables: {project} (git repo or
# folder name, known locally) and {task} (the work label the model generates).
# An empty variable collapses with its adjoining separator (see render_title).
TITLE_TEMPLATE = "{project} - {task}"
```

- [ ] **Step 2: Seed CONFIG and add the knob name**

Change the `CONFIG` dict (around line 78) to include the template:

```python
CONFIG = {
    "enabled": True,
    "override": OVERRIDE_MANUAL_NAMES,
    "interval": INTERVAL,
    "template": TITLE_TEMPLATE,
}
```

Add after `KNOB_INTERVAL` (around line 86):

```python
KNOB_TEMPLATE = "tabnamer_template"
```

- [ ] **Step 3: Add the status-bar knob and wire it in `render`**

In `register_status_bar`, inside the `render` function (around line 508) add after the interval handling:

```python
        template = knobs.get(KNOB_TEMPLATE, CONFIG["template"])
        if isinstance(template, str) and template.strip():
            CONFIG["template"] = template
```

In the `knobs=[...]` list of `StatusBarComponent` (around line 523) add:

```python
                iterm2.StringKnob("Title template", "{project} - {task}", TITLE_TEMPLATE, KNOB_TEMPLATE),
```

(`StringKnob` signature is `(name, placeholder, defaultValue, key)`.)

- [ ] **Step 4: Verify the module still imports**

Run: `python3 -c "import tab_namer; print(tab_namer.CONFIG['template'], tab_namer.KNOB_TEMPLATE)"`
Expected: prints `{project} - {task} tabnamer_template`

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m unittest test_tab_namer -v`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add tab_namer.py
git commit -m "Add configurable title template constant and status bar knob"
```

---

## Task 3: Retune INSTRUCTIONS to emit only the task

**Files:**
- Modify: `tab_namer.py` (`INSTRUCTIONS`, lines 141-192)

- [ ] **Step 1: Rewrite the instructions**

Replace the `INSTRUCTIONS` string body so the model is told it produces only the
distinguishing work label (the project name is added by the caller). Replace
lines 141-192 with:

```python
INSTRUCTIONS = """\
You generate a short label describing the WORK happening in a macOS terminal
tab. Your label is combined by the caller with the project name, so you must
output ONLY the distinguishing work — never the project or folder name itself.

You receive the tab's working directory and its most recent shell commands, and
sometimes a list of other tabs in the same project. Infer the SPECIFIC thing
being worked on — a feature, a bug, a service, a file, a dataset, a deploy — and
name that work.

Rules:
- Output ONLY the label. No preamble, no explanation, no quotes, no
  punctuation, no trailing period.
- Exactly 2 to 4 words, Title Case. Never a single word.
- Name the WORK, not the tooling and not the project name. Prefer "Webhook
  Retry Fix" over "Running Pytest" and over "Payments Api".
- Never begin with filler words: Terminal, Directory, Folder, Label, Tab,
  Session, Name, Title, or Running.
- Stay grounded in what you are given. Do not invent specifics that the
  directory and commands do not support.
- When other tabs are listed, make THIS label clearly distinct from them,
  emphasizing what is unique about this tab's work.

Examples:

This tab:
  directory: payments-api (subdir: src/webhooks)
  recent commands:
    - pytest tests/test_stripe.py
    - git commit -m "retry failed webhooks"
Webhook Retry Fix

This tab:
  directory: blog
  recent commands:
    - npm run build
    - vercel deploy --prod
Production Deploy

This tab:
  directory: kernel (subdir: drivers/net)
  recent commands:
    - make modules
    - dmesg | grep eth0
Net Driver Debugging

This tab:
  directory: notes
  recent commands:
    - vim 2026-budget.md
Annual Budget Notes"""
```

- [ ] **Step 2: Verify the module still imports and the sentinel constant is intact**

Run: `python3 -c "import tab_namer; print('PROMPT_SENTINEL' in dir(tab_namer), len(tab_namer.INSTRUCTIONS) > 0)"`
Expected: prints `True True`

- [ ] **Step 3: Commit**

```bash
git add tab_namer.py
git commit -m "Retune model instructions to emit only the task label"
```

---

## Task 4: Compose the name via render_title in maybe_rename

**Files:**
- Modify: `tab_namer.py` (`maybe_rename`, lines 420-468)

- [ ] **Step 1: Replace the title-building block**

In `maybe_rename`, replace the block that currently computes `title` (lines
451-460, from `if not cmds:` through the `if not title: return`) with:

```python
    project = dir_label
    if not cmds:
        task = ""
    else:
        prompt = build_prompt(
            build_self_block(cwd, meta["root"], cmds),
            build_sibling_block(sib_pairs),
        )
        task = clean_model_label(await run_namer(prompt))
    title = render_title(CONFIG["template"], project, task)
    if not title:
        return
```

(`dir_label` is already computed earlier in the function via
`directory_label(cwd, meta["root"])`. The previous `dir_label` fallback is now
expressed as the empty-`task` collapse inside `render_title`.)

- [ ] **Step 2: Verify the module still imports**

Run: `python3 -c "import tab_namer"`
Expected: no output, exit 0

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m unittest test_tab_namer -v`
Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add tab_namer.py
git commit -m "Compose tab name from template in maybe_rename"
```

---

## Task 5: Length-prefixed framing helpers + resident NamerProcess

**Files:**
- Modify: `tab_namer.py` (replace `run_namer`, lines 344-360; add framing helpers + `NamerProcess` above it)
- Test: `test_tab_namer.py`

- [ ] **Step 1: Write failing tests for the frame encoder**

Add to `test_tab_namer.py`:

```python
def test_encode_frame_roundtrip_ascii(self):
    payload = "hello world"
    frame = tab_namer.encode_frame(payload)
    # decimal byte count, newline, then the bytes
    self.assertEqual(frame, b"11\nhello world")

def test_encode_frame_multibyte(self):
    payload = "café"  # 'é' is 2 bytes in UTF-8 -> 5 bytes total
    frame = tab_namer.encode_frame(payload)
    self.assertEqual(frame, b"5\ncaf\xc3\xa9")

def test_encode_frame_multiline(self):
    payload = "line1\nline2"
    frame = tab_namer.encode_frame(payload)
    self.assertEqual(frame, b"11\nline1\nline2")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_tab_namer.py -k encode_frame -v`
Expected: FAIL with `AttributeError: module 'tab_namer' has no attribute 'encode_frame'`

- [ ] **Step 3: Implement the framing helpers**

Add to `tab_namer.py` above `run_namer` (around line 343):

```python
def encode_frame(text):
    """Encode a string as a length-prefixed frame: b"<nbytes>\\n<bytes>"."""
    body = text.encode("utf-8")
    return str(len(body)).encode("ascii") + b"\n" + body


async def read_frame(stream):
    """Read one length-prefixed frame from an asyncio StreamReader.

    Returns the decoded string, or None on EOF / malformed header.
    """
    header = await stream.readline()
    if not header:
        return None
    try:
        n = int(header.strip())
    except ValueError:
        return None
    body = await stream.readexactly(n)
    return body.decode("utf-8", errors="replace")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_tab_namer.py -k encode_frame -v`
Expected: PASS (3)

- [ ] **Step 5: Add the NamerProcess manager and rewrite run_namer**

Replace the existing `run_namer` (now `async def run_namer(prompt)`, lines
344-360) with the manager class and a thin `run_namer` that uses a module-level
singleton:

```python
class NamerProcess:
    """Owns a single resident `tabnamer` subprocess.

    The helper loads Apple's Foundation Model once and answers framed requests
    for the daemon's lifetime, so the model never reloads per call. Requests are
    serialized (the model is one resource) and the process is respawned lazily
    after any failure.
    """

    def __init__(self):
        self._proc = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        self._proc = await asyncio.create_subprocess_exec(
            NAMER_BIN,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return self._proc

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def ask(self, payload):
        """Send one framed request, return the raw response string ("" on error)."""
        async with self._lock:
            try:
                proc = await self._ensure()
                proc.stdin.write(encode_frame(payload))
                await proc.stdin.drain()
                result = await asyncio.wait_for(
                    read_frame(proc.stdout), timeout=NAMER_TIMEOUT
                )
                if result is None:  # EOF: the helper died
                    self._kill()
                    return ""
                return result
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError,
                    asyncio.IncompleteReadError, OSError) as exc:
                print(f"namer process error: {exc}")
                self._kill()
                return ""


_NAMER = NamerProcess()


async def run_namer(prompt):
    payload = f"{INSTRUCTIONS}\n{PROMPT_SENTINEL}\n{prompt}"
    return await _NAMER.ask(payload)
```

- [ ] **Step 6: Verify the module imports and tests still pass**

Run: `python3 -c "import tab_namer; print(type(tab_namer._NAMER).__name__)"`
Expected: prints `NamerProcess`

Run: `python3 -m unittest test_tab_namer -v`
Expected: PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add tab_namer.py test_tab_namer.py
git commit -m "Talk to a resident namer process via length-prefixed frames"
```

---

## Task 6: Make tabnamer.swift a resident framed loop

**Files:**
- Modify: `tabnamer.swift` (full rewrite of the I/O structure, lines 14-51)

- [ ] **Step 1: Rewrite the binary as a read-eval loop**

Replace the body after the imports (lines 14-51) with:

```swift
// Read one length-prefixed frame from stdin: a decimal byte count, a newline,
// then exactly that many UTF-8 bytes. Returns nil on EOF.
func readFrame(_ handle: FileHandle) -> String? {
    var headerBytes = [UInt8]()
    while true {
        let b = handle.readData(ofLength: 1)
        if b.isEmpty { return nil }            // EOF
        let byte = b[b.startIndex]
        if byte == 0x0A { break }              // newline ends the header
        headerBytes.append(byte)
    }
    guard let header = String(bytes: headerBytes, encoding: .utf8),
          let n = Int(header.trimmingCharacters(in: .whitespaces)), n >= 0 else {
        return nil
    }
    var body = Data()
    while body.count < n {
        let chunk = handle.readData(ofLength: n - body.count)
        if chunk.isEmpty { return nil }        // truncated
        body.append(chunk)
    }
    return String(data: body, encoding: .utf8)
}

func writeFrame(_ text: String) {
    let body = Data(text.utf8)
    let header = Data("\(body.count)\n".utf8)
    FileHandle.standardOutput.write(header)
    FileHandle.standardOutput.write(body)
}

let sentinel = "<<<PROMPT>>>"

let model = SystemLanguageModel.default
guard case .available = model.availability else {
    FileHandle.standardError.write(Data("model unavailable\n".utf8))
    exit(2)
}
model.prewarm()

let stdin = FileHandle.standardInput
while let input = readFrame(stdin) {
    let trimmedAll = input.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmedAll.isEmpty {
        writeFrame("")
        continue
    }

    let instructionsText: String
    let promptText: String
    if let r = input.range(of: sentinel) {
        instructionsText = String(input[..<r.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
        promptText = String(input[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    } else {
        instructionsText = ""
        promptText = trimmedAll
    }

    if promptText.isEmpty {
        writeFrame("")
        continue
    }

    do {
        let session: LanguageModelSession
        if instructionsText.isEmpty {
            session = LanguageModelSession()
        } else {
            session = LanguageModelSession { instructionsText }
        }
        let options = GenerationOptions(temperature: 0.3)
        let response = try await session.respond(to: promptText, options: options)
        writeFrame(response.content)
    } catch {
        FileHandle.standardError.write(Data("error: \(error)\n".utf8))
        writeFrame("")   // keep the loop alive; the daemon treats "" as skip
    }
}
```

Note: a fresh `LanguageModelSession` is created per request so tabs don't share
context; the model weights stay resident in the process, so there is no reload.

- [ ] **Step 2: Build the binary**

Run: `swiftc -O tabnamer.swift -o tabnamer`
Expected: compiles with no errors, produces `./tabnamer`

- [ ] **Step 3: Smoke-test the loop with two framed requests**

Run (sends two prompts back-to-back; `printf` computes the byte lengths inline):

```bash
python3 - <<'PY' | ./tabnamer | python3 - <<'PY2'
PY
PY2
```

If the above is awkward, use this single-request check instead:

```bash
python3 -c '
import sys
p = "name a tab"
sys.stdout.buffer.write(f"{len(p.encode())}\n".encode() + p.encode())
' | ./tabnamer | head -c 200 | xxd | head
```

Expected: the process prints a length-prefixed frame (a decimal number, a
newline, then a short label) and then exits at EOF. A non-empty label confirms
the model loaded and answered.

- [ ] **Step 4: Commit**

```bash
git add tabnamer.swift tabnamer
git commit -m "Make tabnamer a resident framed request loop"
```

---

## Task 7: End-to-end verification

**Files:** none (manual verification)

- [ ] **Step 1: Reinstall / reload the daemon**

Run: `./install.sh`
Expected: builds the binary, reloads the launchd agent, prints "Done."

- [ ] **Step 2: Confirm one resident helper, not one-per-call**

Open several tabs, run a few real commands in each, wait one sweep (~90s), then:

Run: `pgrep -fl tabnamer`
Expected: at most ONE `tabnamer` process (the resident helper), even with
multiple tabs being named. Before this change there would be repeated short-lived
processes.

- [ ] **Step 3: Confirm the model is not reloading per call**

Open Activity Monitor (or `sudo powermetrics --samplers tasks -n 1 | grep -i tabnamer`)
while tabs name themselves.
Expected: no repeated spikes from the model loading on every sweep; the helper
stays resident.

- [ ] **Step 4: Confirm templated names**

Expected: named tabs read as `<project> - <Task>` (e.g. `iterm-tab-namer - Resident Helper Loop`).
Tabs with only navigation commands read as just `<project>`.

- [ ] **Step 5: Confirm custom template via status bar**

Settings > Profiles > Session > Configure Status Bar, edit the "Tab Namer"
component's "Title template" knob to `{project}/{task}`.
Expected: subsequent names use the new separator.

- [ ] **Step 6: Check logs for errors**

Run: `tail -n 40 tabnamer.log`
Expected: no repeated `namer process error` lines; no tracebacks.

---

## Self-Review Notes

- **Spec coverage:** Part 1 (resident helper) → Tasks 5–6; framing → Task 5 (Python) + Task 6 (Swift); lifecycle/resilience → Task 5 `NamerProcess`. Part 2 (templates): config/knob → Task 2; instruction retune → Task 3; `render_title` → Task 1; `maybe_rename` composition → Task 4. End-to-end → Task 7. `install.sh` unchanged, as specified.
- **Type/name consistency:** `render_title(template, project, task)`, `encode_frame`, `read_frame`, `NamerProcess.ask`, `_NAMER`, `run_namer(prompt)`, `CONFIG["template"]`, `KNOB_TEMPLATE`/`"tabnamer_template"` are used consistently across tasks.
- **No placeholders:** every code step shows complete code; commands show expected output.

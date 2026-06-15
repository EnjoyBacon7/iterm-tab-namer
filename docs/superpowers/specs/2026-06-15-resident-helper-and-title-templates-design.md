# Resident Naming Helper + Templatable Tab Titles

**Date:** 2026-06-15
**Status:** Approved design, pending implementation plan

## Problem

Two issues with the current iTerm2 auto tab namer:

1. **Power drain.** The Python daemon (`tab_namer.py`) spawns a fresh `tabnamer`
   Swift process for every tab on every sweep (`run_namer` →
   `create_subprocess_exec`). Each process loads Apple's on-device Foundation
   Model from cold, runs a single inference, and exits. The model never stays
   warm, so repeated model loads are visible in Activity Monitor and burn power.

2. **Titles are not templatable.** The model returns the entire tab label. There
   is no way to express a desired shape such as `{project} - {task}`, even though
   the daemon already knows the project name locally (`directory_label`).

## Goals

- Keep Apple's Foundation Model loaded once and reuse it across all naming
  requests, eliminating per-call model loads.
- Let the user define the tab-title shape with a template using the variables
  `{project}` and `{task}`, defaulting to `{project} - {task}`.
- Preserve existing behavior: human-set names are respected, names only change
  when context/siblings change, pure logic stays iTerm2-free and unit-tested.

## Non-goals

- Rewriting the daemon in Swift.
- Additional template variables beyond `{project}` and `{task}` (e.g. `{branch}`,
  `{subdir}`, `{dir}`). Explicitly deferred.

## Part 1 — Resident naming helper

### Approach

`tabnamer.swift` becomes a persistent read-eval loop instead of a one-shot
process:

1. At startup, check `SystemLanguageModel.default` availability and prewarm.
2. Loop: read one framed request from stdin, build a response, write one framed
   response to stdout, flush. Exit cleanly on EOF.

The Python daemon spawns the helper once (lazily, on first naming), holds the
process handle, and communicates over its stdin/stdout pipes. The model stays
resident for the daemon's lifetime; launchd keeps the daemon alive as today.

Rejected alternatives:
- **Rewrite daemon in Swift** — discards the working Python/iTerm2 integration.
- **`prewarm()` only** — the process still exits each call, so the model still
  unloads. Does not solve the problem.

### Wire protocol

Length-prefixed framing (prompts are multi-line, so newlines can't delimit
records):

- **Request:** a decimal byte count, then `\n`, then exactly that many UTF-8
  bytes. The payload is the existing format unchanged:
  `INSTRUCTIONS` + `\n<<<PROMPT>>>\n` + per-tab prompt.
- **Response:** same framing — decimal byte count, `\n`, then the model's label
  bytes.

### Session isolation

For each request the loop creates a **fresh** `LanguageModelSession` (with the
instructions from that request). Model weights remain resident in the process;
only the lightweight session object is new. This preserves per-tab independence
(no cross-tab context contamination) while avoiding the reload cost.

### Lifecycle & resilience (Python side)

- A helper-manager holds the subprocess handle and an `asyncio.Lock` that
  serializes requests (the model is a single resource; sweeps already run
  sequentially).
- `run_namer(prompt)` keeps its current signature and contract: returns the raw
  label string, or `""` on any failure. Internally it now writes a framed
  request to the resident process and reads a framed response, under the lock and
  under `NAMER_TIMEOUT`.
- On timeout, EOF, broken pipe, or non-zero exit: kill the handle, return `""`
  (one naming skipped, exactly like today), and respawn lazily on the next call.
- The helper exits on stdin EOF, so it dies with the daemon.

## Part 2 — Templatable titles

### Configuration

- New module constant `TITLE_TEMPLATE = "{project} - {task}"` near `INTERVAL`.
- Mirrored into `CONFIG["template"]`.
- New status-bar knob `StringKnob("Title template", TITLE_TEMPLATE,
  KNOB_TEMPLATE)` (`KNOB_TEMPLATE = "tabnamer_template"`) so it is editable live
  from iTerm2's Configure Component dialog, alongside enabled/override/interval.
  `render` writes the knob value into `CONFIG["template"]`.

### Variables

- `{project}` — the existing `directory_label(cwd, project_root)` value: git repo
  basename, else folder basename; empty string for `$HOME`.
- `{task}` — the model's output. The model's job narrows to producing only the
  distinguishing work description; the daemon prepends the project.

### Instruction changes

`INSTRUCTIONS` in both `tab_namer.py` and the embedded examples are retuned to
ask for **only the distinguishing work** (still 2–4 words, Title Case, no
project/folder name in the output), since the daemon now composes the project in
via the template. Sibling-differentiation guidance is unchanged.

### Composition

New pure, unit-tested function:

```
render_title(template, project, task) -> str
```

Rules:
- Substitute `{project}` and `{task}`.
- If `task` is empty: drop the `{task}` token **and** its adjoining separator,
  yielding a clean project-only name.
- If `project` is empty: collapse to just the task.
- Collapse internal whitespace and trim.
- Apply `MAX_TITLE_LEN` to the **final composed** string so `project - task`
  never overflows the cap.

`maybe_rename` builds `project` from `directory_label`, gets `task` from
`clean_model_label(run_namer(...))` (or `""`), then sets the name to
`render_title(CONFIG["template"], project, task)`. The thin-context path (no
meaningful commands) renders the template with an empty task, i.e. just the
project — replacing today's direct `dir_label` assignment.

## Testing

`test_tab_namer.py` gains:
- `render_title`: both filled; empty task collapses separator; empty project
  collapses to task; whitespace handling; final length cap.
- Retuned `clean_model_label` / instruction expectations (task-only labels).

All pure functions remain importable without `iterm2`.

## Files touched

- `tabnamer.swift` — persistent loop + framed I/O + prewarm.
- `tab_namer.py` — helper-manager + framed `run_namer`, `TITLE_TEMPLATE`/
  `CONFIG["template"]`/knob, retuned `INSTRUCTIONS`, `render_title`, updated
  `maybe_rename`.
- `test_tab_namer.py` — new tests.
- `install.sh` — unchanged (same `swiftc -O` build, same launchd setup).

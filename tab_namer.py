#!/usr/bin/env python3
"""iTerm2 auto tab namer.

A daemon (an iTerm2 AutoLaunch script) that periodically renames tabs based on
their content. For each tab it builds a small context from the working
directory and the last few commands run, then asks Apple's on-device Foundation
Models (via the `tabnamer` helper binary) for a short label. Tabs in the same
project (git repo root, else cwd) are named with awareness of each other so two
tabs working on the same project but different problems get distinct names.

Rules:
  - Never overwrite a name a human set by hand: once a tab's name differs from
    what we last set it to, we leave that tab alone for the rest of the session.
  - Only (re)name a tab when its context changed, or when its set of siblings
    changed (so it can differentiate from a newcomer).

The pure helper functions below have no iTerm2 dependency and are unit-tested in
test_tab_namer.py. The `iterm2` import is guarded so the module can be imported
outside iTerm2's runtime for testing.
"""

import asyncio
import json
import os
import re
import signal
import sys
from collections import deque

try:
    import iterm2
except Exception:  # pragma: no cover - only available inside iTerm2's runtime
    iterm2 = None

# ---- configuration ----
INTERVAL = 90          # seconds between naming sweeps
MAX_COMMANDS = 3       # number of recent commands fed to the model
MAX_TITLE_LEN = 28     # hard cap on the model's {task} label length
MAX_FULL_TITLE_LEN = 48  # hard cap on the final composed "{project} - {task}"
NAMER_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tabnamer")
NAMER_TIMEOUT = 20     # seconds to wait for the model

# Template for the final tab name. Supported variables: {project} (git repo or
# folder name, known locally) and {task} (the work label the model generates).
# An empty variable collapses with its adjoining separator (see render_title).
TITLE_TEMPLATE = "{project} - {task}"

# When True, manage every tab's name even if a human renamed it (the daemon
# re-asserts its own name). When False (default), a tab a human renamed is left
# alone for the rest of the session.
OVERRIDE_MANUAL_NAMES = False

# Names we treat as "not human-set", so we're free to claim the tab. The shell
# job name and the profile name are added per-session at runtime.
DEFAULT_NAMES = {"", "zsh", "-zsh", "bash", "-bash", "fish", "Shell", "login"}

# Commands that carry no signal about what a tab is for — navigation and
# housekeeping. A tab whose only activity is these is named after its directory
# rather than sent to the model (which produces noise from such thin context).
TRIVIAL_COMMANDS = {
    "cd", "ls", "ll", "la", "l", "pwd", "clear", "cls", "exit", "z", "j",
    "popd", "pushd", "dirs", "history", "..", "...", "cd..",
}

# The small on-device model often wraps its answer in boilerplate ("Terminal:
# Dev", "Directory Downloads", "Terminal Label: ...") or refuses outright. We
# strip these prefixes and reject non-labels so junk never becomes a tab name.
_META_PREFIX_RE = re.compile(
    r"^(?:terminal label|terminal|directory|folder|label|tab name|tab|name|title)"
    r"\s*[:\-–]?\s+",
    re.IGNORECASE,
)
_GARBAGE_EXACT = {
    "terminal", "directory", "folder", "label", "tab", "name", "title",
    "untitled", "shell", "apple", "none", "unknown", "na",
}
_GARBAGE_SUBSTRINGS = (
    "no applicable", "foundation model", "i can't", "i cannot",
    "cannot determine", "unable to", "as an ai", "sorry",
)
_HOME = os.path.expanduser("~")

# Config lives in a JSON file the user edits directly or via the `config`
# subcommand. It is read once at daemon startup (restart to apply changes).
_CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.join(_HOME, ".config")),
    "iterm-tab-namer",
)
CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")
PID_PATH = os.path.join(_CONFIG_DIR, "daemon.pid")

# The user-settable keys, in display order.
CONFIG_KEYS = ("enabled", "override", "interval", "template")

# Runtime config, seeded from the constants above and overlaid at startup with
# load_config(CONFIG_PATH).
CONFIG = {
    "enabled": True,
    "override": OVERRIDE_MANUAL_NAMES,
    "interval": INTERVAL,
    "template": TITLE_TEMPLATE,
}


def config_defaults():
    """The built-in config, used when the file is missing keys."""
    return {
        "enabled": True,
        "override": OVERRIDE_MANUAL_NAMES,
        "interval": float(INTERVAL),
        "template": TITLE_TEMPLATE,
    }


def load_config(path):
    """Return the defaults overlaid with the known keys found in the JSON file.

    A missing file yields the defaults; a malformed file yields the defaults
    with a warning. Unknown keys in the file are ignored.
    """
    cfg = config_defaults()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return cfg
    except (ValueError, OSError) as exc:
        print(f"config: ignoring {path} ({exc}); using defaults")
        return cfg
    if isinstance(data, dict):
        for k in CONFIG_KEYS:
            if k in data:
                cfg[k] = data[k]
    return cfg


def save_config(path, cfg):
    """Write only the known keys to the JSON file, creating the directory."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {k: cfg[k] for k in CONFIG_KEYS if k in cfg}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def coerce_value(key, raw):
    """Convert a CLI string to the typed, validated value for `key`.

    Raises ValueError with a user-facing message on a bad key or value.
    """
    if key not in CONFIG_KEYS:
        raise ValueError(f"unknown key: {key} (valid: {', '.join(CONFIG_KEYS)})")
    if key in ("enabled", "override"):
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{key} must be true or false")
    if key == "interval":
        try:
            val = float(raw)
        except ValueError:
            raise ValueError("interval must be a number")
        if val < 10:
            raise ValueError("interval must be >= 10 seconds")
        return val
    # template
    text = raw.strip()
    if not text:
        raise ValueError("template must not be empty")
    if "{project}" not in text and "{task}" not in text:
        raise ValueError("template must contain {project} and/or {task}")
    return text


def format_value(value):
    """Render a config value for display (lowercase bools, int-like floats)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def write_pid_file(path):
    """Record this process's PID so `trigger` can find the daemon."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def read_pid_file(path):
    """Return the PID stored in the file, or None if missing/malformed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def is_process_alive(pid):
    """Whether a process with this PID currently exists."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not signalable by us
    except OSError:
        return False
    return True


# ----------------------------------------------------------------------------
# Pure logic (no iTerm2 dependency) — unit tested.
# ----------------------------------------------------------------------------
def basename(path):
    if not path:
        return ""
    return os.path.basename(path.rstrip("/")) or path


def context_signature(cwd, commands):
    """A stable string identifying a tab's content, for change detection."""
    return (cwd or "") + "||" + "\n".join(commands[-MAX_COMMANDS:])


def build_self_block(cwd, project_root, commands):
    """Render the 'this tab' section of the prompt."""
    location = basename(project_root) if project_root else basename(cwd)
    sub = ""
    if project_root and cwd and cwd != project_root:
        rel = os.path.relpath(cwd, project_root)
        if rel and rel != ".":
            sub = f" (subdir: {rel})"
    lines = [f"  directory: {location or '~'}{sub}"]
    recent = [c for c in commands[-MAX_COMMANDS:] if c]
    if recent:
        lines.append("  recent commands:")
        lines.extend(f"    - {c}" for c in recent)
    return "\n".join(lines)


def build_sibling_block(siblings):
    """Render the sibling-tabs section. `siblings` is a list of (name, last_cmd)."""
    if not siblings:
        return ""
    out = [
        "Other tabs in the same project. Give THIS tab a label clearly "
        "different from these, focusing on what makes this tab's work distinct:"
    ]
    for name, last in siblings:
        last = last or "(no command yet)"
        out.append(f'  - "{name}" — last: {last}')
    return "\n".join(out)


# Delimiter between the instructions and the per-tab prompt on the namer's
# stdin (must match the sentinel in tabnamer.swift).
PROMPT_SENTINEL = "<<<PROMPT>>>"

# The model's system instructions. Apple's Foundation Models obey instructions
# over anything in the prompt, so all the durable guidance lives here. The
# examples are written in the exact format build_self_block/build_prompt emit,
# which is what keeps this small model on-task.
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


def build_prompt(self_block, sibling_block):
    parts = ["This tab:", self_block]
    if sibling_block:
        parts += ["", sibling_block]
    return "\n".join(parts)


def sanitize_title(raw):
    """Reduce a raw model response to a single short clean label."""
    if not raw:
        return ""
    stripped = raw.strip()
    line = stripped.splitlines()[0] if stripped else ""
    # Strip surrounding quotes and punctuation from both ends in one pass.
    line = line.strip(" \t\"'`.!,:;")
    line = " ".join(line.split())
    if len(line) > MAX_TITLE_LEN:
        line = line[:MAX_TITLE_LEN].rstrip()
    return line


def meaningful_commands(commands):
    """Drop navigation/housekeeping commands that say nothing about the work."""
    out = []
    for c in commands:
        c = (c or "").strip()
        if not c:
            continue
        if c.split()[0] in TRIVIAL_COMMANDS:
            continue
        out.append(c)
    return out


def directory_label(cwd, project_root):
    """A clean, deterministic tab name derived from the folder (no model)."""
    path = project_root or cwd or ""
    if path and os.path.abspath(path) == _HOME:
        return ""  # don't name a tab after the home directory
    base = basename(project_root) if project_root else basename(cwd)
    base = (base or "").strip()
    if base in ("", "~"):
        return ""
    if len(base) > MAX_TITLE_LEN:
        base = base[:MAX_TITLE_LEN].rstrip()
    return base


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
    if len(out) > MAX_FULL_TITLE_LEN:
        out = out[:MAX_FULL_TITLE_LEN].rstrip()
    return out


def clean_model_label(raw):
    """Turn a raw model response into a usable label, or "" if it's not one.

    Strips boilerplate prefixes the small model tends to add and rejects
    refusals / generic non-answers so they never get applied as a tab name.
    """
    if not raw or not raw.strip():
        return ""
    line = raw.strip().splitlines()[0].strip(" \t\"'`.!,:;")
    line = " ".join(line.split())
    # Strip leading boilerplate, possibly stacked ("Terminal Label: Directory X").
    prev = None
    while line and line != prev:
        prev = line
        line = _META_PREFIX_RE.sub("", line).strip()
    label = sanitize_title(line)  # final trim / collapse / length cap
    if not label:
        return ""
    low = label.lower()
    if len(low) < 2 or low in _GARBAGE_EXACT:
        return ""
    if any(s in low for s in _GARBAGE_SUBSTRINGS):
        return ""
    return label


def is_free_to_name(name, job, profile, our_name, override=False):
    """Whether we may set this tab's name.

    `our_name` is the last name we set (None if we never named it). A tab whose
    name no longer matches what we set has been taken over by a human. When
    `override` is True we manage every tab regardless of human edits.
    """
    if override:
        return True
    n = (name or "").strip()
    if our_name is not None:
        return n == our_name.strip()
    if n in DEFAULT_NAMES:
        return True
    if job and n == job.strip():
        return True
    if profile and n == profile.strip():
        return True
    return n == ""


def extract_command(result):
    """Pull a command string out of a PromptMonitor.async_get() result."""
    try:
        if isinstance(result, str):
            return result if result.strip() else None
        if isinstance(result, (tuple, list)):
            for item in result:
                if isinstance(item, str) and item.strip():
                    return item
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------------
# iTerm2 glue.
# ----------------------------------------------------------------------------
class SessionState:
    def __init__(self):
        self.commands = deque(maxlen=MAX_COMMANDS)
        self.last_sig = None
        self.last_sibling_sig = None
        self.our_name = None  # last name we set; None until we name it


class Daemon:
    def __init__(self):
        self.sessions = {}     # session_id -> SessionState
        self.hands_off = set()  # session_ids a human took over
        self.monitors = {}     # session_id -> asyncio.Task
        self.git_cache = {}    # cwd -> repo root (or None)


async def git_root(cwd, cache):
    if not cwd:
        return None
    if cwd in cache:
        return cache[cwd]
    root = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cwd, "rev-parse", "--show-toplevel",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        if proc.returncode == 0:
            root = out.decode().strip() or None
    except Exception:
        root = None
    cache[cwd] = root
    return root


def encode_frame(text):
    r"""Encode a string as a length-prefixed frame: b"<nbytes>\n<bytes>"."""
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


async def monitor_commands(connection, daemon, session_id):
    """Maintain a rolling buffer of the last few commands run in a session."""
    try:
        async with iterm2.PromptMonitor(
            connection,
            session_id,
            modes=[iterm2.PromptMonitor.Mode.COMMAND_START],
        ) as mon:
            while True:
                result = await mon.async_get()
                cmd = extract_command(result)
                if cmd:
                    st = daemon.sessions.setdefault(session_id, SessionState())
                    st.commands.append(cmd.strip())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # PromptMonitor may be unsupported; degrade quietly
        print(f"command monitor ended for {session_id}: {exc}")


def ensure_monitor(connection, daemon, session_id):
    if session_id in daemon.monitors:
        return
    daemon.sessions.setdefault(session_id, SessionState())
    daemon.monitors[session_id] = asyncio.create_task(
        monitor_commands(connection, daemon, session_id)
    )


def cleanup(daemon, session_id):
    task = daemon.monitors.pop(session_id, None)
    if task:
        task.cancel()
    daemon.sessions.pop(session_id, None)
    daemon.hands_off.discard(session_id)


async def gather_meta(daemon, session):
    sid = session.session_id
    cwd = await session.async_get_variable("path")
    job = await session.async_get_variable("jobName")
    name = (await session.async_get_variable("name")) or ""
    profile = await session.async_get_variable("profileName")
    st = daemon.sessions.setdefault(sid, SessionState())
    # A human took this tab over → hands off for the rest of the session
    # (unless we're configured to override manual names).
    if not CONFIG["override"] and st.our_name is not None and name != st.our_name:
        daemon.hands_off.add(sid)
        return None
    root = await git_root(cwd, daemon.git_cache)
    return {
        "sid": sid, "session": session, "cwd": cwd, "job": job,
        "name": name, "profile": profile, "root": root, "st": st,
        "cmds": list(st.commands),
    }


async def maybe_rename(meta, group):
    st = meta["st"]
    if not is_free_to_name(
        meta["name"], meta["job"], meta["profile"], st.our_name,
        override=CONFIG["override"],
    ):
        return
    cwd = meta["cwd"]
    cmds = meaningful_commands(meta["cmds"])
    dir_label = directory_label(cwd, meta["root"])
    if not cwd and not cmds:
        return

    sig = context_signature(cwd, cmds)
    siblings = [o for o in group if o["sid"] != meta["sid"]]
    sib_pairs = [
        (o["name"], (meaningful_commands(o["cmds"]) or [""])[-1]) for o in siblings
    ]
    sib_sig = tuple(sorted(n for n, _ in sib_pairs))
    changed = st.last_sig != sig or st.last_sibling_sig != sib_sig
    # In override mode, also re-name if a human changed the displayed name, so
    # the daemon re-asserts control even when the content itself didn't change.
    if CONFIG["override"] and st.our_name is not None and meta["name"] != st.our_name:
        changed = True
    if not changed:
        return

    # With no real activity yet, leave the task empty so the title collapses to
    # just the project (asking the model produces noise from such thin context).
    # Once meaningful commands exist, ask the model for the {task}; if it returns
    # boilerplate or a refusal the empty task collapses to the project too.
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
    try:
        await meta["session"].async_set_name(title)
    except Exception as exc:
        print(f"set_name failed for {meta['sid']}: {exc}")
        return
    st.our_name = title
    st.last_sig = sig
    st.last_sibling_sig = sib_sig


async def sweep(app, daemon):
    metas = []
    for window in app.windows:
        for tab in window.tabs:
            for session in tab.sessions:
                if session.session_id in daemon.hands_off:
                    continue
                meta = await gather_meta(daemon, session)
                if meta:
                    metas.append(meta)

    groups = {}
    for meta in metas:
        key = meta["root"] or meta["cwd"] or meta["sid"]
        groups.setdefault(key, []).append(meta)

    for group in groups.values():
        for meta in group:
            await maybe_rename(meta, group)


async def main(connection):
    app = await iterm2.async_get_app(connection)
    daemon = Daemon()

    CONFIG.update(load_config(CONFIG_PATH))
    try:
        write_pid_file(PID_PATH)
    except OSError as exc:
        print(f"could not write pid file {PID_PATH}: {exc}")

    # `trigger` sends SIGUSR1 to ask for an immediate, off-interval sweep.
    sweep_now = asyncio.Event()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, sweep_now.set)
    except (NotImplementedError, ValueError, RuntimeError) as exc:
        print(f"manual trigger unavailable (no SIGUSR1 handler): {exc}")

    for window in app.windows:
        for tab in window.tabs:
            for session in tab.sessions:
                ensure_monitor(connection, daemon, session.session_id)

    async def watch_new():
        async with iterm2.NewSessionMonitor(connection) as mon:
            while True:
                sid = await mon.async_get()
                ensure_monitor(connection, daemon, sid)

    async def watch_term():
        async with iterm2.SessionTerminationMonitor(connection) as mon:
            while True:
                sid = await mon.async_get()
                cleanup(daemon, sid)

    asyncio.create_task(watch_new())
    asyncio.create_task(watch_term())

    try:
        while True:
            # A manual trigger sweeps even when auto-naming is disabled, so the
            # user can always test on demand; the timer respects `enabled`.
            triggered = sweep_now.is_set()
            sweep_now.clear()
            try:
                if CONFIG["enabled"] or triggered:
                    await sweep(app, daemon)
            except Exception as exc:
                print(f"sweep error: {exc}")
            try:
                await asyncio.wait_for(sweep_now.wait(), timeout=CONFIG["interval"])
            except asyncio.TimeoutError:
                pass
    finally:
        try:
            os.remove(PID_PATH)
        except OSError:
            pass


def _cmd_config(argv):
    """Handle `config get|set|list|path`. Returns a process exit code."""
    sub = argv[0] if argv else "list"
    if sub == "path":
        print(CONFIG_PATH)
        return 0
    if sub == "list":
        cfg = load_config(CONFIG_PATH)
        for key in CONFIG_KEYS:
            print(f"{key} = {format_value(cfg[key])}")
        print(f"# file: {CONFIG_PATH}")
        return 0
    if sub == "get":
        if len(argv) < 2 or argv[1] not in CONFIG_KEYS:
            print(f"usage: config get <{'|'.join(CONFIG_KEYS)}>", file=sys.stderr)
            return 2
        print(format_value(load_config(CONFIG_PATH)[argv[1]]))
        return 0
    if sub == "set":
        if len(argv) < 3:
            print("usage: config set <key> <value>", file=sys.stderr)
            return 2
        try:
            value = coerce_value(argv[1], argv[2])
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        cfg = load_config(CONFIG_PATH)
        cfg[argv[1]] = value
        save_config(CONFIG_PATH, cfg)
        print(f"{argv[1]} = {format_value(value)}")
        print("Run `brew services restart iterm-tab-namer` to apply.")
        return 0
    print(f"unknown config command: {sub} (use get/set/list/path)", file=sys.stderr)
    return 2


def _cmd_trigger():
    """Ask the running daemon to sweep now. Returns a process exit code."""
    pid = read_pid_file(PID_PATH)
    if not is_process_alive(pid):
        print(
            "daemon not running — start it with `brew services start iterm-tab-namer`",
            file=sys.stderr,
        )
        return 1
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError as exc:
        print(f"could not signal daemon (pid {pid}): {exc}", file=sys.stderr)
        return 1
    print("Triggered a naming sweep.")
    return 0


def _cli(argv):
    """Entry point. Subcommands manage config / trigger; no args runs the daemon."""
    if argv and argv[0] == "config":
        return _cmd_config(argv[1:])
    if argv and argv[0] == "trigger":
        return _cmd_trigger()
    if argv:
        print(
            f"unknown command: {argv[0]} "
            "(use `config`, `trigger`, or no arguments to run the daemon)",
            file=sys.stderr,
        )
        return 2
    if iterm2 is None:
        print("the 'iterm2' package is not available; cannot run the daemon",
              file=sys.stderr)
        return 1
    iterm2.run_forever(main)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

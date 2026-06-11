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
import os
from collections import deque

try:
    import iterm2
except Exception:  # pragma: no cover - only available inside iTerm2's runtime
    iterm2 = None

# ---- configuration ----
INTERVAL = 90          # seconds between naming sweeps
MAX_COMMANDS = 3       # number of recent commands fed to the model
MAX_TITLE_LEN = 28     # hard cap on the generated label length
NAMER_BIN = os.path.expanduser("~/github/iterm-tab-namer/tabnamer")
NAMER_TIMEOUT = 20     # seconds to wait for the model

# Names we treat as "not human-set", so we're free to claim the tab. The shell
# job name and the profile name are added per-session at runtime.
DEFAULT_NAMES = {"", "zsh", "-zsh", "bash", "-bash", "fish", "Shell", "login"}


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


def build_prompt(self_block, sibling_block):
    parts = [
        "You name terminal tabs with a short, specific label.",
        "",
        "This tab:",
        self_block,
    ]
    if sibling_block:
        parts += ["", sibling_block]
    parts += [
        "",
        "Reply with ONLY a 2-4 word Title Case label. "
        "No quotes, no punctuation, no explanation.",
    ]
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


def is_free_to_name(name, job, profile, our_name):
    """Whether we may set this tab's name.

    `our_name` is the last name we set (None if we never named it). A tab whose
    name no longer matches what we set has been taken over by a human.
    """
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


async def run_namer(prompt):
    try:
        proc = await asyncio.create_subprocess_exec(
            NAMER_BIN,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(
            proc.communicate(prompt.encode()), timeout=NAMER_TIMEOUT
        )
        if proc.returncode != 0:
            return ""
        return out.decode(errors="replace")
    except Exception:
        return ""


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
    # A human took this tab over → hands off for the rest of the session.
    if st.our_name is not None and name != st.our_name:
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
    if not is_free_to_name(meta["name"], meta["job"], meta["profile"], st.our_name):
        return
    cwd, cmds = meta["cwd"], meta["cmds"]
    if not cwd and not cmds:
        return

    sig = context_signature(cwd, cmds)
    siblings = [o for o in group if o["sid"] != meta["sid"]]
    sib_pairs = [(o["name"], o["cmds"][-1] if o["cmds"] else "") for o in siblings]
    sib_sig = tuple(sorted(n for n, _ in sib_pairs))
    if st.last_sig == sig and st.last_sibling_sig == sib_sig:
        return

    prompt = build_prompt(
        build_self_block(cwd, meta["root"], cmds),
        build_sibling_block(sib_pairs),
    )
    title = sanitize_title(await run_namer(prompt))
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

    while True:
        try:
            await sweep(app, daemon)
        except Exception as exc:
            print(f"sweep error: {exc}")
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__" and iterm2 is not None:
    iterm2.run_forever(main)

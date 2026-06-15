"""Unit tests for the pure logic in tab_namer (no iTerm2 needed)."""
import contextlib
import io
import os
import signal
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tab_namer as tn


class TestSanitizeTitle(unittest.TestCase):
    def test_strips_quotes_and_punctuation(self):
        self.assertEqual(tn.sanitize_title('"Auth Bug".'), "Auth Bug")
        self.assertEqual(tn.sanitize_title("`DB Migration`"), "DB Migration")

    def test_takes_first_line(self):
        self.assertEqual(tn.sanitize_title("Build Failure\nsome rambling"), "Build Failure")

    def test_collapses_whitespace(self):
        self.assertEqual(tn.sanitize_title("  Run   Tests  "), "Run Tests")

    def test_truncates_to_max_len(self):
        out = tn.sanitize_title("x" * 100)
        self.assertLessEqual(len(out), tn.MAX_TITLE_LEN)

    def test_empty(self):
        self.assertEqual(tn.sanitize_title(""), "")
        self.assertEqual(tn.sanitize_title("   "), "")


class TestIsFreeToName(unittest.TestCase):
    def test_default_name_is_claimable(self):
        self.assertTrue(tn.is_free_to_name("zsh", "zsh", "Default", None))
        self.assertTrue(tn.is_free_to_name("", "zsh", "Default", None))

    def test_job_name_is_claimable(self):
        self.assertTrue(tn.is_free_to_name("node", "node", "Default", None))

    def test_profile_name_is_claimable(self):
        self.assertTrue(tn.is_free_to_name("Default", "zsh", "Default", None))

    def test_unknown_existing_name_is_manual(self):
        self.assertFalse(tn.is_free_to_name("My Important Tab", "zsh", "Default", None))

    def test_owned_unchanged_is_claimable(self):
        self.assertTrue(tn.is_free_to_name("Auth Bug", "zsh", "Default", "Auth Bug"))

    def test_owned_then_changed_is_manual(self):
        self.assertFalse(tn.is_free_to_name("Human Renamed", "zsh", "Default", "Auth Bug"))

    def test_override_claims_manual_names(self):
        self.assertTrue(
            tn.is_free_to_name("Human Renamed", "zsh", "Default", "Auth Bug", override=True)
        )
        self.assertTrue(
            tn.is_free_to_name("My Important Tab", "zsh", "Default", None, override=True)
        )


class TestContextSignature(unittest.TestCase):
    def test_changes_with_cwd(self):
        a = tn.context_signature("/a", ["ls"])
        b = tn.context_signature("/b", ["ls"])
        self.assertNotEqual(a, b)

    def test_changes_with_commands(self):
        a = tn.context_signature("/a", ["ls"])
        b = tn.context_signature("/a", ["ls", "git status"])
        self.assertNotEqual(a, b)

    def test_stable(self):
        self.assertEqual(
            tn.context_signature("/a", ["ls"]),
            tn.context_signature("/a", ["ls"]),
        )


class TestBuildBlocks(unittest.TestCase):
    def test_self_block_includes_project_and_subdir(self):
        block = tn.build_self_block("/repo/src/auth", "/repo", ["pytest"])
        self.assertIn("directory: repo", block)
        self.assertIn("subdir: src/auth", block)
        self.assertIn("- pytest", block)

    def test_self_block_no_project(self):
        block = tn.build_self_block("/home/me/notes", None, [])
        self.assertIn("directory: notes", block)
        self.assertNotIn("subdir", block)
        self.assertNotIn("recent commands", block)

    def test_sibling_block_lists_names_and_commands(self):
        block = tn.build_sibling_block([("Auth Bug", "pytest tests/auth"), ("DB Work", "")])
        self.assertIn('"Auth Bug" — last: pytest tests/auth', block)
        self.assertIn("(no command yet)", block)

    def test_sibling_block_empty(self):
        self.assertEqual(tn.build_sibling_block([]), "")

    def test_prompt_assembles(self):
        prompt = tn.build_prompt(
            tn.build_self_block("/repo", "/repo", ["make"]),
            tn.build_sibling_block([("Other", "ls")]),
        )
        self.assertIn("This tab:", prompt)
        self.assertIn("Other tabs in the same project", prompt)

    def test_prompt_without_siblings(self):
        prompt = tn.build_prompt(tn.build_self_block("/repo", "/repo", ["make"]), "")
        self.assertNotIn("Other tabs in the same project", prompt)

    def test_prompt_carries_only_data(self):
        # Guidance lives in INSTRUCTIONS, not the per-tab prompt.
        prompt = tn.build_prompt(tn.build_self_block("/repo", "/repo", ["make"]), "")
        self.assertNotIn("Output ONLY", prompt)
        self.assertNotIn("Title Case", prompt)


class TestInstructions(unittest.TestCase):
    def test_states_format_and_constraints(self):
        ins = tn.INSTRUCTIONS
        self.assertIn("2 to 4 words", ins)
        self.assertIn("Title Case", ins)
        self.assertIn("Output ONLY", ins)

    def test_includes_few_shot_examples(self):
        self.assertIn("Net Driver Debugging", tn.INSTRUCTIONS)

    def test_tells_model_to_omit_project_name(self):
        ins = tn.INSTRUCTIONS
        self.assertIn("never the project or folder name", ins.lower())

    def test_examples_use_real_input_format(self):
        # Examples must mirror what build_self_block emits, or they mislead.
        self.assertIn("recent commands:", tn.INSTRUCTIONS)
        self.assertIn("directory:", tn.INSTRUCTIONS)


class TestMeaningfulCommands(unittest.TestCase):
    def test_drops_navigation_and_noise(self):
        self.assertEqual(
            tn.meaningful_commands(["cd ~/proj", "ls -la", "clear", "pwd"]),
            [],
        )

    def test_keeps_real_work(self):
        self.assertEqual(
            tn.meaningful_commands(["cd ~/proj", "pytest tests", "git status"]),
            ["pytest tests", "git status"],
        )

    def test_skips_blanks(self):
        self.assertEqual(tn.meaningful_commands(["", "  ", "make"]), ["make"])


class TestDirectoryLabel(unittest.TestCase):
    def test_uses_project_root_basename(self):
        self.assertEqual(tn.directory_label("/repo/src/auth", "/repo"), "repo")

    def test_falls_back_to_cwd_basename(self):
        self.assertEqual(tn.directory_label("/Users/me/Documents/taxes", None), "taxes")

    def test_empty_for_no_path(self):
        self.assertEqual(tn.directory_label("", None), "")

    def test_empty_for_home(self):
        self.assertEqual(tn.directory_label(os.path.expanduser("~"), None), "")

    def test_truncates(self):
        out = tn.directory_label("/" + "x" * 100, None)
        self.assertLessEqual(len(out), tn.MAX_TITLE_LEN)


class TestCleanModelLabel(unittest.TestCase):
    def test_passes_clean_label(self):
        self.assertEqual(tn.clean_model_label("Auth Bug"), "Auth Bug")

    def test_strips_meta_prefix(self):
        self.assertEqual(tn.clean_model_label("Terminal: Dev"), "Dev")
        self.assertEqual(tn.clean_model_label("Directory Downloads"), "Downloads")
        self.assertEqual(tn.clean_model_label("Terminal Label: Build Failure"), "Build Failure")

    def test_rejects_refusals_and_meta(self):
        for bad in [
            "No applicable tags detected",
            "Apple Foundation Model",
            "Terminal",
            "Directory",
            "I can't determine a label",
            "",
        ]:
            self.assertEqual(tn.clean_model_label(bad), "", f"should reject: {bad!r}")


class TestRenderTitle(unittest.TestCase):
    def test_both_filled(self):
        self.assertEqual(
            tn.render_title("{project} - {task}", "payments-api", "Webhook Retry Fix"),
            "payments-api - Webhook Retry Fix",
        )

    def test_empty_task_collapses_separator(self):
        self.assertEqual(
            tn.render_title("{project} - {task}", "payments-api", ""),
            "payments-api",
        )

    def test_empty_project_collapses_to_task(self):
        self.assertEqual(
            tn.render_title("{project} - {task}", "", "Webhook Retry Fix"),
            "Webhook Retry Fix",
        )

    def test_both_empty(self):
        self.assertEqual(tn.render_title("{project} - {task}", "", ""), "")

    def test_collapses_whitespace(self):
        self.assertEqual(
            tn.render_title("{project}  -  {task}", "repo", "Do  Thing"),
            "repo - Do Thing",
        )

    def test_caps_final_length(self):
        long_proj = "averylongrepositoryname"
        out = tn.render_title(
            "{project} - {task}", long_proj, "Some Very Long Task Description Here"
        )
        self.assertLessEqual(len(out), tn.MAX_FULL_TITLE_LEN)

    def test_custom_template(self):
        self.assertEqual(
            tn.render_title("{project}/{task}", "repo", "Fix Bug"),
            "repo/Fix Bug",
        )


class TestEncodeFrame(unittest.TestCase):
    def test_roundtrip_ascii(self):
        self.assertEqual(tn.encode_frame("hello world"), b"11\nhello world")

    def test_multibyte(self):
        # 'é' is 2 bytes in UTF-8 -> 5 bytes total
        self.assertEqual(tn.encode_frame("café"), b"5\ncaf\xc3\xa9")

    def test_multiline(self):
        self.assertEqual(tn.encode_frame("line1\nline2"), b"11\nline1\nline2")


class TestLoadConfig(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_missing_file_returns_defaults(self):
        cfg = tn.load_config("/no/such/path/config.json")
        self.assertEqual(cfg, tn.config_defaults())

    def test_valid_file_overlays_defaults(self):
        path = self._write('{"template": "{project}", "interval": 120}')
        cfg = tn.load_config(path)
        self.assertEqual(cfg["template"], "{project}")
        self.assertEqual(cfg["interval"], 120)
        self.assertEqual(cfg["enabled"], True)  # default preserved

    def test_malformed_file_returns_defaults(self):
        path = self._write("{not valid json")
        with contextlib.redirect_stdout(io.StringIO()):
            cfg = tn.load_config(path)
        self.assertEqual(cfg, tn.config_defaults())

    def test_unknown_keys_ignored(self):
        path = self._write('{"bogus": 1, "enabled": false}')
        cfg = tn.load_config(path)
        self.assertNotIn("bogus", cfg)
        self.assertEqual(cfg["enabled"], False)


class TestSaveConfig(unittest.TestCase):
    def test_roundtrip_only_known_keys(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        path = os.path.join(d, "sub", "config.json")  # dir created on save
        tn.save_config(path, {"enabled": False, "template": "{task}", "junk": 9})
        self.assertTrue(os.path.exists(path))
        cfg = tn.load_config(path)
        self.assertEqual(cfg["enabled"], False)
        self.assertEqual(cfg["template"], "{task}")
        with open(path) as f:
            self.assertNotIn("junk", f.read())


class TestCoerceValue(unittest.TestCase):
    def test_bools(self):
        self.assertIs(tn.coerce_value("enabled", "true"), True)
        self.assertIs(tn.coerce_value("enabled", "FALSE"), False)
        self.assertIs(tn.coerce_value("override", "on"), True)
        self.assertIs(tn.coerce_value("override", "0"), False)

    def test_bad_bool(self):
        with self.assertRaises(ValueError):
            tn.coerce_value("enabled", "maybe")

    def test_unknown_key(self):
        with self.assertRaises(ValueError):
            tn.coerce_value("nope", "x")

    def test_interval_numeric(self):
        self.assertEqual(tn.coerce_value("interval", "120"), 120.0)

    def test_interval_rejects_below_minimum(self):
        with self.assertRaises(ValueError):
            tn.coerce_value("interval", "5")

    def test_interval_rejects_non_number(self):
        with self.assertRaises(ValueError):
            tn.coerce_value("interval", "soon")

    def test_template_requires_placeholder(self):
        with self.assertRaises(ValueError):
            tn.coerce_value("template", "no placeholders here")

    def test_template_rejects_empty(self):
        with self.assertRaises(ValueError):
            tn.coerce_value("template", "   ")

    def test_template_valid(self):
        self.assertEqual(tn.coerce_value("template", " {project}/{task} "), "{project}/{task}")


class TestPidHelpers(unittest.TestCase):
    def _tmp_path(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return os.path.join(d, "daemon.pid")

    def test_write_then_read(self):
        path = self._tmp_path()
        tn.write_pid_file(path)
        self.assertEqual(tn.read_pid_file(path), os.getpid())

    def test_read_missing_returns_none(self):
        self.assertIsNone(tn.read_pid_file("/no/such/daemon.pid"))

    def test_read_malformed_returns_none(self):
        path = self._tmp_path()
        with open(path, "w") as f:
            f.write("not-a-pid")
        self.assertIsNone(tn.read_pid_file(path))

    def test_alive_for_self_dead_for_bogus(self):
        self.assertTrue(tn.is_process_alive(os.getpid()))
        self.assertFalse(tn.is_process_alive(999999))
        self.assertFalse(tn.is_process_alive(None))
        self.assertFalse(tn.is_process_alive(0))


class TestConfigCli(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self._orig = tn.CONFIG_PATH
        tn.CONFIG_PATH = os.path.join(d, "config.json")
        self.addCleanup(lambda: setattr(tn, "CONFIG_PATH", self._orig))

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = tn._cli(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_set_then_get(self):
        rc, _, _ = self._run(["config", "set", "template", "{project}: {task}"])
        self.assertEqual(rc, 0)
        rc, out, _ = self._run(["config", "get", "template"])
        self.assertEqual(rc, 0)
        self.assertIn("{project}: {task}", out)

    def test_set_bad_value_exits_2(self):
        rc, _, err = self._run(["config", "set", "interval", "3"])
        self.assertEqual(rc, 2)
        self.assertIn("interval", err)

    def test_set_bad_key_exits_2(self):
        rc, _, err = self._run(["config", "set", "nope", "x"])
        self.assertEqual(rc, 2)

    def test_list_shows_all_keys(self):
        rc, out, _ = self._run(["config", "list"])
        self.assertEqual(rc, 0)
        for key in tn.CONFIG_KEYS:
            self.assertIn(key, out)

    def test_path_prints_config_path(self):
        rc, out, _ = self._run(["config", "path"])
        self.assertEqual(rc, 0)
        self.assertIn(tn.CONFIG_PATH, out)

    def test_unknown_command_exits_2(self):
        rc, _, _ = self._run(["frobnicate"])
        self.assertEqual(rc, 2)


class TestTriggerCli(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self._orig = tn.PID_PATH
        tn.PID_PATH = os.path.join(d, "daemon.pid")
        self.addCleanup(lambda: setattr(tn, "PID_PATH", self._orig))

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = tn._cli(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_no_daemon_exits_1(self):
        rc, _, err = self._run(["trigger"])
        self.assertEqual(rc, 1)
        self.assertIn("not running", err)

    def test_signals_running_daemon(self):
        # Stand in as the daemon: install a SIGUSR1 handler, write our PID, and
        # confirm `trigger` delivers the signal. (Default SIGUSR1 would kill us.)
        received = []
        old = signal.signal(signal.SIGUSR1, lambda *a: received.append(1))
        self.addCleanup(lambda: signal.signal(signal.SIGUSR1, old))
        tn.write_pid_file(tn.PID_PATH)
        rc, out, _ = self._run(["trigger"])
        self.assertEqual(rc, 0)
        self.assertIn("Triggered", out)
        self.assertEqual(received, [1])


class TestLogging(unittest.TestCase):
    def setUp(self):
        import logging
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self._orig_path = tn.LOG_PATH
        tn.LOG_PATH = os.path.join(d, "tab-namer.log")

        def reset_logger():
            lg = logging.getLogger("iterm-tab-namer")
            for h in list(lg.handlers):
                h.close()
                lg.removeHandler(h)
            tn._logger = None

        reset_logger()
        self.addCleanup(lambda: setattr(tn, "LOG_PATH", self._orig_path))
        self.addCleanup(reset_logger)

    def test_log_writes_to_file(self):
        tn.log("hello world")
        with open(tn.LOG_PATH) as f:
            self.assertIn("hello world", f.read())

    def test_handler_is_size_capped(self):
        tn.log("init")  # builds the logger
        handlers = [
            h for h in tn._logger.handlers
            if isinstance(h, tn.RotatingFileHandler)
        ]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].maxBytes, tn.LOG_MAX_BYTES)
        self.assertEqual(handlers[0].backupCount, tn.LOG_BACKUPS)
        self.assertGreater(tn.LOG_MAX_BYTES, 0)
        self.assertGreater(tn.LOG_BACKUPS, 0)


class _Mode:  # stand-in for iterm2.PromptMonitor.Mode (an enum, never a str)
    pass


class TestExtractCommand(unittest.TestCase):
    def test_tuple_with_command(self):
        self.assertEqual(tn.extract_command((_Mode(), "git status")), "git status")

    def test_plain_string(self):
        self.assertEqual(tn.extract_command("ls -la"), "ls -la")

    def test_none_when_no_string(self):
        self.assertIsNone(tn.extract_command((_Mode(), None)))
        self.assertIsNone(tn.extract_command(None))


if __name__ == "__main__":
    unittest.main()

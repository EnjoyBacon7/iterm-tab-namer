"""Unit tests for the pure logic in tab_namer (no iTerm2 needed)."""
import os
import sys
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

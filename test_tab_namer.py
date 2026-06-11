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
        self.assertIn("2-4 word", prompt)

    def test_prompt_without_siblings(self):
        prompt = tn.build_prompt(tn.build_self_block("/repo", "/repo", ["make"]), "")
        self.assertNotIn("Other tabs in the same project", prompt)


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

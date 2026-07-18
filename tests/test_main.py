import unittest
from unittest.mock import patch

from mackup_ng import main


class TestMain(unittest.TestCase):
    def test_main_header(self):
        with patch("mackup_ng.utils.supports_color_output", return_value=True):
            assert main.header("blah") == "\033[34mblah\033[0m"
        with patch("mackup_ng.utils.supports_color_output", return_value=False):
            assert main.header("blah") == "blah"

    def test_main_bold(self):
        with patch("mackup_ng.utils.supports_color_output", return_value=True):
            assert main.bold("blah") == "\033[1mblah\033[0m"
        with patch("mackup_ng.utils.supports_color_output", return_value=False):
            assert main.bold("blah") == "blah"

    def test_get_action_label_returns_none_when_no_activity(self):
        assert main.get_action_label({"backed_up": 0, "skipped": 0, "errors": 0}) is None

    def test_get_action_label_returns_skipped_for_real_skip(self):
        assert main.get_action_label({"backed_up": 0, "skipped": 1, "errors": 0}) == "Skipped"

    def test_get_action_label_returns_failed_when_only_errors(self):
        assert main.get_action_label({"backed_up": 0, "skipped": 0, "errors": 1}) == "Failed"

    def test_get_action_label_returns_failed_when_partial_success_has_errors(self):
        assert main.get_action_label({"backed_up": 1, "skipped": 0, "errors": 1}) == "Failed"

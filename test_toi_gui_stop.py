"""Unit tests for GUI process-tree termination helper."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from toi_gui import terminate_process_tree


class TerminateProcessTreeTests(unittest.TestCase):
    def test_noop_when_already_exited(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = 0
        with patch("toi_gui.subprocess.run") as run_mock:
            terminate_process_tree(proc)
        run_mock.assert_not_called()
        proc.terminate.assert_not_called()

    def test_windows_uses_taskkill_tree(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows-only path")
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        with patch("toi_gui.subprocess.run") as run_mock:
            terminate_process_tree(proc)
        run_mock.assert_called_once()
        args = run_mock.call_args[0][0]
        self.assertEqual(args[:4], ["taskkill", "/F", "/T", "/PID"])
        self.assertEqual(args[4], "12345")
        proc.wait.assert_called()

    def test_unix_terminate_then_wait(self) -> None:
        if sys.platform == "win32":
            self.skipTest("Unix-only path")
        proc = MagicMock()
        proc.poll.return_value = None
        terminate_process_tree(proc, timeout=0.1)
        proc.terminate.assert_called_once()
        proc.wait.assert_called()


if __name__ == "__main__":
    unittest.main()

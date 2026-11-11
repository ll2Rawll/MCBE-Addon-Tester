import ctypes
import os
import tempfile
import unittest
from unittest.mock import Mock

from mcbe_tester.desktop import Desktop, DesktopUnavailable


class NativeBoundaryTests(unittest.TestCase):
    def test_key_release_on_interrupted_hold(self):
        desktop = Desktop.__new__(Desktop)
        desktop.guard = Mock()
        desktop.key_event = Mock()
        desktop.wait = Mock(side_effect=DesktopUnavailable("Focus lost"))
        with self.assertRaises(DesktopUnavailable):
            desktop.press(["w", "shift"], 1)
        calls = desktop.key_event.call_args_list
        self.assertEqual([c.args[0] for c in calls], ["w", "shift", "shift", "w"])
        self.assertTrue(calls[-1].kwargs["up"])

    def test_mouse_release_on_interrupted_hold(self):
        desktop = Desktop.__new__(Desktop)
        desktop.guard = Mock()
        desktop.mouse_event = Mock()
        desktop.wait = Mock(side_effect=DesktopUnavailable("Stopped"))
        with self.assertRaises(DesktopUnavailable):
            desktop.click(seconds=1)
        self.assertEqual([c.args[0] for c in desktop.mouse_event.call_args_list], [2, 4])

    @unittest.skipUnless(os.name == "nt", "Requires Windows APIs, not a game session")
    def test_win32_structure_size_and_readonly_discovery(self):
        with tempfile.TemporaryDirectory() as root:
            desktop = Desktop(root + "/STOP")
            self.assertEqual(ctypes.sizeof(desktop.Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
            self.assertIsInstance(desktop.windows(), list)

    @unittest.skipIf(os.name == "nt", "Non-Windows boundary")
    def test_non_windows_explicitly_blocked(self):
        with self.assertRaises(DesktopUnavailable):
            Desktop("/tmp/STOP")

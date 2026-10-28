"""Tests for the command-line parsing in main.py."""

import unittest

from water_system import config
from water_system.main import main, parse_args


class MainArgsTests(unittest.TestCase):
    def test_defaults_are_safe(self):
        args = parse_args([])
        self.assertEqual(args.host, "127.0.0.1")     # local only by default
        self.assertFalse(args.memory)
        self.assertFalse(args.blocked)
        self.assertIsNone(args.soil)

    def test_flags_are_parsed(self):
        args = parse_args(["--memory", "--blocked", "--soil", "20",
                           "--host", "0.0.0.0", "--port", "8080"])
        self.assertTrue(args.memory)
        self.assertTrue(args.blocked)
        self.assertEqual(args.soil, 20.0)
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8080)

    def test_default_port_comes_from_config(self):
        self.assertEqual(parse_args([]).port, config.FLASK_PORT)


if __name__ == "__main__":
    unittest.main()
    
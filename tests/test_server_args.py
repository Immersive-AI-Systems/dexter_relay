import contextlib
import io
import unittest
from unittest.mock import patch

from dexter_relay.server import _create_source, build_parser


class ServerArgumentTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_default_source_is_ble(self):
        args = self.parser.parse_args([])

        self.assertEqual(args.source, "ble")

    def test_all_sources_are_available(self):
        for source in ("ble", "serial", "ipad", "recording", "simulation"):
            args = self.parser.parse_args(["--source", source])
            self.assertEqual(args.source, source)

    def test_recording_requires_path(self):
        args = self.parser.parse_args(["--source", "recording"])

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(
            SystemExit
        ) as raised:
            _create_source(args, self.parser)

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("requires --recording PATH", stderr.getvalue())

    def test_serial_uses_existing_dexter_controller_source(self):
        args = self.parser.parse_args(
            ["--source", "serial", "--map", "COM20:thumb,index"]
        )

        with patch("dexter_relay.server.DexterForceSource") as source_class:
            source = _create_source(args, self.parser)

        self.assertIs(source, source_class.return_value)
        self.assertFalse(source_class.call_args.kwargs["use_ble"])
        self.assertEqual(
            source_class.call_args.kwargs["mapping_specs"],
            ["COM20:thumb,index"],
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import threading

from dexter_relay.source import DexterForceSource, parse_mapping_specs, visualizer_ble_finger_slices


class MappingParserTests(unittest.TestCase):
    def test_visualizer_ble_finger_order(self):
        payload = list(range(15))
        self.assertEqual(
            visualizer_ble_finger_slices(payload),
            {
                "thumb": [12, 13, 14],
                "index": [9, 10, 11],
                "middle": [6, 7, 8],
                "ring": [3, 4, 5],
                "pinky": [0, 1, 2],
            },
        )

    def test_parse_mapping_specs(self):
        parsed = parse_mapping_specs(
            ["COM20:thumb,index", "/dev/ttyUSB1:middle,ring", "COM8:pinky"]
        )
        self.assertEqual(
            parsed.by_port,
            {
                "COM20": ("thumb", "index"),
                "/dev/ttyUSB1": ("middle", "ring"),
                "COM8": ("pinky",),
            },
        )

    def test_rejects_duplicate_finger(self):
        with self.assertRaisesRegex(ValueError, "duplicate finger"):
            parse_mapping_specs(["COM20:thumb,index", "COM5:index"])

    def test_rejects_too_many_fingers_per_serial_device(self):
        with self.assertRaisesRegex(ValueError, "at most two"):
            parse_mapping_specs(["COM20:thumb,index,middle"])


class BleDownsampleTests(unittest.TestCase):
    def _make_source(self) -> DexterForceSource:
        source = DexterForceSource.__new__(DexterForceSource)
        source._ble_sample_sequence = 0
        source._ble_lock = threading.Lock()
        source._ble_raw_by_name = {}
        source._ble_has_data = {}
        source._ble_last_update_ts = {}
        source._ble_counter = None
        source._ble_timestamp_us = None
        source._device = MagicMock()
        return source

    def test_accepts_latest_event_from_queue(self):
        source = self._make_source()
        events = [
            SimpleNamespace(timestamp_us=1_000_000, payload=list(range(15)), counter=1),
            SimpleNamespace(timestamp_us=1_020_000, payload=list(range(15)), counter=2),
        ]
        source._device.get_events.return_value = events

        self.assertTrue(source._accept_latest_ble_event(100.0))
        self.assertEqual(source._ble_timestamp_us, 1_020_000)
        self.assertEqual(source._ble_sample_sequence, 1)

    def test_skips_when_queue_empty(self):
        source = self._make_source()
        source._device.get_events.return_value = []

        self.assertFalse(source._accept_latest_ble_event(100.0))
        self.assertEqual(source._ble_sample_sequence, 0)


class BleReconnectTests(unittest.TestCase):
    def _make_source(self) -> DexterForceSource:
        source = DexterForceSource.__new__(DexterForceSource)
        source._ble_lock = threading.Lock()
        source._ble_stop_event = threading.Event()
        source._ble_connection_state = "connected"
        source._ble_reconnect_count = 0
        source._ble_last_error = None
        source._ble_last_event_monotonic = 1.0
        source._ble_next_reconnect_monotonic = 0.0
        source._ble_reconnect_initial_delay = 1.0
        source._ble_reconnect_max_delay = 8.0
        source._ble_reconnect_delay = 1.0
        source._ble_connect_args = {
            "scan_timeout": 5.0,
            "retries": 3,
            "ble_address": "CA:2B:20:4E:8E:0D",
            "ble_adapter": "auto",
        }
        source._device = MagicMock()
        return source

    @patch("dexter_relay.source._connect_ble_device")
    def test_reconnect_replaces_and_closes_stale_device(self, connect):
        source = self._make_source()
        old_device = source._device
        new_device = MagicMock()
        connect.return_value = new_device

        self.assertTrue(source._maybe_reconnect_ble(10.0))

        old_device.close.assert_called_once_with()
        connect.assert_called_once_with(
            scan_timeout=5.0,
            retries=1,
            ble_address="CA:2B:20:4E:8E:0D",
            ble_adapter="auto",
        )
        self.assertIs(source._device, new_device)
        self.assertEqual(source._ble_connection_state, "connected")
        self.assertEqual(source._ble_reconnect_count, 1)

    @patch("dexter_relay.source._connect_ble_device")
    def test_failed_reconnect_uses_exponential_backoff(self, connect):
        source = self._make_source()
        connect.side_effect = RuntimeError("radio unavailable")

        self.assertFalse(source._maybe_reconnect_ble(10.0))

        self.assertIsNone(source._device)
        self.assertEqual(source._ble_connection_state, "disconnected")
        self.assertIn("radio unavailable", source._ble_last_error)
        self.assertEqual(source._ble_reconnect_delay, 2.0)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
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


if __name__ == "__main__":
    unittest.main()

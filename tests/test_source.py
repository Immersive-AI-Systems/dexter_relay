import unittest

from dexter_relay.source import parse_mapping_specs, visualizer_ble_finger_slices


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


if __name__ == "__main__":
    unittest.main()

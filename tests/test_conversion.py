import unittest

from dexter_relay.conversion import (
    compose_force_3,
    compose_force_4,
    compose_raw_4,
    signed_int16,
    signed_int16_values,
)


class ConversionTests(unittest.TestCase):
    def assertSequenceAlmostEqual(self, actual, expected, places=7):
        self.assertEqual(len(actual), len(expected))
        for left, right in zip(actual, expected):
            self.assertAlmostEqual(left, right, places=places)

    def test_signed_int16_matches_visualizer_reinterpret_cast(self):
        self.assertEqual(signed_int16(0), 0)
        self.assertEqual(signed_int16(32767), 32767)
        self.assertEqual(signed_int16(32768), -32768)
        self.assertEqual(signed_int16(65535), -1)
        self.assertEqual(signed_int16(-123), -123)
        self.assertEqual(signed_int16_values([65535, 65534]), (-1, -2))

    def test_compose_raw_4_matches_visualizer_geometry(self):
        self.assertSequenceAlmostEqual(
            compose_raw_4([1000, -2000, 3000, -4000]),
            (-6062.177826491071, 1500.0000000000005, -1000.0),
            places=6,
        )

    def test_compose_force_4_matches_visualizer_defaults(self):
        self.assertSequenceAlmostEqual(
            compose_force_4([1000, -2000, 3000, -4000]),
            (-1.153636725507779, 1.1863251039556006, -2.57079713298117),
            places=6,
        )

    def test_compose_force_3_matches_visualizer_channel_reorder(self):
        self.assertSequenceAlmostEqual(
            compose_force_3([100, 200, 300]),
            (0.04806953851247742, -0.08325888300000017),
            places=6,
        )

    def test_compose_force_3_uses_signed_int16_payloads(self):
        self.assertSequenceAlmostEqual(
            compose_force_3([65535, 65534, 65533]),
            (-0.0004806953851248519, 0.0008325888299997031),
            places=9,
        )


if __name__ == "__main__":
    unittest.main()

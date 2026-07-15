import unittest

from dexter_relay.ble_device import ble_connect_timeout, ble_startup_timeout


class BleTimeoutTests(unittest.TestCase):
    def test_default_startup_timeout_covers_scan_and_connect(self):
        self.assertEqual(ble_connect_timeout(5.0), 20.0)
        self.assertEqual(ble_startup_timeout(5.0), 30.0)

    def test_long_scan_extends_both_timeouts(self):
        self.assertEqual(ble_connect_timeout(30.0), 35.0)
        self.assertEqual(ble_startup_timeout(30.0), 70.0)


if __name__ == "__main__":
    unittest.main()

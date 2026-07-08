import unittest

from dexter_relay.protocol import (
    decode_datagram,
    encode_datagram,
    is_subscribe_packet,
    is_unsubscribe_packet,
    make_subscribe_packet,
    make_unsubscribe_packet,
)


class ProtocolTests(unittest.TestCase):
    def test_subscribe_packet_round_trip(self):
        packet = make_subscribe_packet()
        decoded = decode_datagram(encode_datagram(packet))
        self.assertTrue(is_subscribe_packet(decoded))
        self.assertFalse(is_unsubscribe_packet(decoded))

    def test_unsubscribe_packet_round_trip(self):
        packet = make_unsubscribe_packet("client-1")
        decoded = decode_datagram(encode_datagram(packet))
        self.assertTrue(is_unsubscribe_packet(decoded))
        self.assertFalse(is_subscribe_packet(decoded))
        self.assertEqual(decoded["client_id"], "client-1")


if __name__ == "__main__":
    unittest.main()

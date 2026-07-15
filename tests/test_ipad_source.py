import json
import socket
import time
import unittest

from dexter_relay.ipad_source import IpadTouchSource, decode_ipad_packet


def touch(
    role: str,
    x: float,
    y: float,
    *,
    touch_id: int,
    active: bool = True,
    state: str = "moved",
) -> dict:
    return {
        "id": touch_id,
        "role": role,
        "x": x,
        "y": y,
        "normalizedX": 0.5,
        "normalizedY": 0.5,
        "active": active,
        "state": state,
    }


def packet(
    sequence: int,
    touches: list[dict],
    *,
    session_id: str = "session-a",
    event: str = "moved",
) -> dict:
    return {
        "protocol": "ipad-dexter-touch",
        "version": 2,
        "coordinateSystem": "target-offset-centimeters",
        "sessionId": session_id,
        "sequence": sequence,
        "timestamp": 1_783_000_000.0 + sequence,
        "monotonicTime": 100.0 + sequence,
        "event": event,
        "touches": touches,
        "view": {"width": 1000.0, "height": 700.0},
        "orientation": "landscapeLeft",
        "calibration": {
            "pointsPerCentimeter": 51.9685,
            "targetSeparationCm": 3.0,
        },
    }


def encode(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


class DecodeIpadPacketTests(unittest.TestCase):
    def test_accepts_protocol_v2_position_packet(self):
        decoded = decode_ipad_packet(
            encode(packet(1, [touch("left", 0.25, -0.5, touch_id=7)]))
        )

        self.assertEqual(decoded.session_id, "session-a")
        self.assertEqual(decoded.sequence, 1)
        self.assertEqual(decoded.touches[0].role, "left")
        self.assertEqual((decoded.touches[0].x, decoded.touches[0].y), (0.25, -0.5))

    def test_rejects_inconsistent_lifecycle(self):
        payload = packet(
            1,
            [touch("left", 0.0, 0.0, touch_id=1, active=True, state="ended")],
            event="ended",
        )

        with self.assertRaisesRegex(ValueError, "ended/cancelled"):
            decode_ipad_packet(encode(payload))


class IpadTouchSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = IpadTouchSource(bind_host="127.0.0.1", port=0)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def tearDown(self):
        self.sender.close()
        self.source.close()

    def send(self, payload: dict | bytes) -> dict:
        before = self.source.read_snapshot()["status"]["ipad"]
        before_total = (
            before["accepted"] + before["invalid"] + before["out_of_order"]
        )
        data = payload if isinstance(payload, bytes) else encode(payload)
        self.sender.sendto(data, self.source.address)
        deadline = time.monotonic() + 0.25
        snapshot = self.source.read_snapshot()
        while time.monotonic() < deadline:
            stats = snapshot["status"]["ipad"]
            total = stats["accepted"] + stats["invalid"] + stats["out_of_order"]
            if total > before_total:
                return snapshot
            time.sleep(0.001)
            snapshot = self.source.read_snapshot()
        return snapshot

    def test_maps_left_and_right_to_index_and_middle(self):
        snapshot = self.send(
            packet(
                1,
                [
                    touch("left", 0.25, -0.5, touch_id=11, state="began"),
                    touch("right", -0.75, 1.5, touch_id=12, state="began"),
                ],
                event="began",
            )
        )

        self.assertEqual(snapshot["transport"], "ipad")
        self.assertEqual(snapshot["measurement_kind"], "position")
        self.assertEqual(snapshot["units"], "cm")
        self.assertEqual(snapshot["fingers"]["index"]["force"], [0.25, -0.5])
        self.assertEqual(snapshot["fingers"]["middle"]["force"], [-0.75, 1.5])
        self.assertTrue(snapshot["fingers"]["index"]["has_data"])
        self.assertFalse(snapshot["fingers"]["thumb"]["has_data"])
        self.assertEqual(
            snapshot["status"]["ipad"]["role_mapping"],
            {"left": "index", "right": "middle"},
        )

    def test_ended_touch_clears_only_its_mapped_finger(self):
        self.send(
            packet(
                1,
                [
                    touch("left", 0.1, 0.2, touch_id=1, state="began"),
                    touch("right", 0.3, 0.4, touch_id=2, state="began"),
                ],
                event="began",
            )
        )
        snapshot = self.send(
            packet(
                2,
                [
                    touch(
                        "left",
                        0.1,
                        0.2,
                        touch_id=1,
                        active=False,
                        state="ended",
                    ),
                    touch("right", 0.3, 0.4, touch_id=2, state="stationary"),
                ],
                event="ended",
            )
        )

        self.assertFalse(snapshot["fingers"]["index"]["has_data"])
        self.assertEqual(snapshot["fingers"]["index"]["touch_state"], "ended")
        self.assertTrue(snapshot["fingers"]["middle"]["has_data"])

    def test_drops_duplicate_sequence_in_same_session(self):
        self.send(packet(2, [touch("left", 1.0, 2.0, touch_id=1)]))
        snapshot = self.send(packet(2, [touch("left", 9.0, 9.0, touch_id=1)]))

        self.assertEqual(snapshot["fingers"]["index"]["force"], [1.0, 2.0])
        self.assertEqual(snapshot["status"]["ipad"]["accepted"], 1)
        self.assertEqual(snapshot["status"]["ipad"]["out_of_order"], 1)

    def test_new_session_clears_previous_touch_state(self):
        self.send(packet(5, [touch("left", 1.0, 2.0, touch_id=1)]))
        snapshot = self.send(
            packet(
                1,
                [touch("right", 3.0, 4.0, touch_id=2, state="began")],
                session_id="session-b",
                event="began",
            )
        )

        self.assertFalse(snapshot["fingers"]["index"]["has_data"])
        self.assertEqual(snapshot["fingers"]["index"]["force"], [])
        self.assertTrue(snapshot["fingers"]["middle"]["has_data"])
        self.assertEqual(snapshot["status"]["ipad"]["session_id"], "session-b")

    def test_delayed_packet_cannot_restore_retired_session(self):
        self.send(packet(5, [touch("left", 1.0, 2.0, touch_id=1)]))
        self.send(
            packet(
                1,
                [touch("right", 3.0, 4.0, touch_id=2, state="began")],
                session_id="session-b",
                event="began",
            )
        )
        snapshot = self.send(
            packet(4, [touch("left", 9.0, 9.0, touch_id=1)])
        )

        self.assertEqual(snapshot["status"]["ipad"]["session_id"], "session-b")
        self.assertFalse(snapshot["fingers"]["index"]["has_data"])
        self.assertEqual(snapshot["fingers"]["middle"]["force"], [3.0, 4.0])
        self.assertEqual(snapshot["status"]["ipad"]["out_of_order"], 1)

    def test_invalid_datagram_is_counted_and_ignored(self):
        snapshot = self.send(b"not-json")

        self.assertEqual(snapshot["status"]["ipad"]["accepted"], 0)
        self.assertEqual(snapshot["status"]["ipad"]["invalid"], 1)

    def test_custom_role_mapping_must_use_distinct_fingers(self):
        with self.assertRaisesRegex(ValueError, "different Dexter fingers"):
            IpadTouchSource(
                bind_host="127.0.0.1",
                port=0,
                role_mapping={"left": "index", "right": "index"},
            )


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from dexter_relay.recording_source import RecordingForceSource


def frame(value: float, *, timestamp: float, sequence: int) -> dict:
    return {
        "type": "force",
        "sequence": sequence,
        "timestamp": timestamp,
        "transport": "ble",
        "measurement_kind": "force",
        "units": "N",
        "fingers": {
            "index": {
                "raw": [1, 2, 3],
                "force": [value, -value],
                "channels": 3,
                "has_data": True,
            }
        },
        "status": {"device": "test"},
    }


class RecordingForceSourceTests(unittest.TestCase):
    def test_reads_json_lines_and_loops(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        frame(1.0, timestamp=100.0, sequence=10),
                        frame(2.0, timestamp=100.05, sequence=11),
                    )
                ),
                encoding="utf-8",
            )
            source = RecordingForceSource(path)

            first = source.read_snapshot()
            second = source.read_snapshot()
            looped = source.read_snapshot()

        self.assertEqual(first["transport"], "recording")
        self.assertEqual(first["fingers"]["index"]["force"], [1.0, -1.0])
        self.assertFalse(first["fingers"]["thumb"]["has_data"])
        self.assertEqual(second["fingers"]["index"]["force"], [2.0, -2.0])
        self.assertEqual(looped["fingers"]["index"]["force"], [1.0, -1.0])
        self.assertEqual(first["status"]["recording"]["recorded_sequence"], 10)
        self.assertEqual(first["status"]["recording"]["original_transport"], "ble")

    def test_accepts_json_array_and_can_hold_last_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(
                json.dumps(
                    [
                        frame(1.0, timestamp=100.0, sequence=1),
                        frame(3.0, timestamp=100.1, sequence=2),
                    ]
                ),
                encoding="utf-8",
            )
            source = RecordingForceSource(path, loop=False)

            source.read_snapshot()
            final = source.read_snapshot()
            held = source.read_snapshot()

        self.assertEqual(final["fingers"]["index"]["force"], [3.0, -3.0])
        self.assertEqual(held["fingers"]["index"]["force"], [3.0, -3.0])
        self.assertFalse(held["status"]["recording"]["loop"])

    def test_accepts_object_with_frames_array(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(
                json.dumps(
                    {"frames": [frame(4.0, timestamp=100.0, sequence=1)]}
                ),
                encoding="utf-8",
            )

            snapshot = RecordingForceSource(path).read_snapshot()

        self.assertEqual(snapshot["fingers"]["index"]["force"], [4.0, -4.0])

    def test_rejects_empty_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.jsonl"
            path.write_text("\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "is empty"):
                RecordingForceSource(path)


if __name__ == "__main__":
    unittest.main()

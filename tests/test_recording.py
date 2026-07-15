import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dexter_relay.recording import (
    CSV_COLUMNS,
    CsvPlaybackSource,
    recording_path,
    write_frame_row,
)


def sample_frame(sequence: int, raw_value: int) -> dict:
    return {
        "type": "force",
        "version": 1,
        "sequence": sequence,
        "timestamp": 1000.0 + sequence,
        "transport": "ble",
        "fingers": {
            "thumb": {
                "raw": [raw_value, 2, 3],
                "force": [0.1, 0.2],
                "channels": 3,
                "has_data": True,
                "last_update_ts": 1000.0,
                "age_s": 0.01,
            }
        },
        "status": {"ble": {"sample_sequence": sequence}},
    }


class RecordingTests(unittest.TestCase):
    def test_recording_filename_contains_timestamp(self):
        path = recording_path(
            "recording", datetime(2026, 7, 15, 12, 34, 56, 123456)
        )
        self.assertEqual(
            path, Path("recording/dexter_20260715_123456_123456.csv")
        )

    def test_playback_loops_rows_and_preserves_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                write_frame_row(writer, sample_frame(10, 111), recorded_at=2000.0)
                write_frame_row(writer, sample_frame(11, 222), recorded_at=2000.05)

            source = CsvPlaybackSource(path)
            first = source.read_snapshot()
            second = source.read_snapshot()
            looped = source.read_snapshot()

        self.assertEqual(first["transport"], "playback")
        self.assertEqual(first["fingers"]["thumb"]["raw"][0], 111)
        self.assertEqual(second["fingers"]["thumb"]["raw"][0], 222)
        self.assertEqual(looped["fingers"]["thumb"]["raw"][0], 111)
        self.assertEqual(looped["status"]["playback"]["loop_count"], 1)
        self.assertEqual(looped["status"]["playback"]["source_sequence"], 10)
        self.assertEqual(looped["fingers"]["thumb"]["age_s"], 0.0)

    def test_empty_recording_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=CSV_COLUMNS).writeheader()

            with self.assertRaisesRegex(ValueError, "no data rows"):
                CsvPlaybackSource(path)


if __name__ == "__main__":
    unittest.main()

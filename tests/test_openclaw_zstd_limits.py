"""Hard event-output/window budgets with real available zstd backends."""
import builtins
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

try:
    from .test_codex_limits import USAGE
except ImportError:
    from test_codex_limits import USAGE
try:
    from compression import zstd as STDLIB
except ImportError:
    STDLIB = None
try:
    import zstandard as OPTIONAL
except ImportError:
    OPTIONAL = None


class OpenClawZstdLimitTests(unittest.TestCase):
    def require_backend(self, backend):
        if backend == "stdlib" and STDLIB is None:
            self.skipTest("stdlib zstd unavailable")
        if backend == "optional" and OPTIONAL is None:
            self.skipTest("optional zstandard unavailable")

    @contextlib.contextmanager
    def backend(self, backend):
        self.require_backend(backend)
        original_import = builtins.__import__

        def select(name, *args, **kwargs):
            if backend == "optional" and name in ("compression", "compression.zstd"):
                raise ImportError("exercise installed optional backend")
            if backend == "stdlib" and name == "zstandard":
                raise AssertionError("stdlib must be preferred")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=select), \
             mock.patch.object(USAGE, "_OPENCLAW_ZSTD_AVAILABLE", None):
            yield

    def encode(self, payload, known):
        if OPTIONAL is not None:
            return OPTIONAL.ZstdCompressor(write_content_size=known).compress(payload)
        if STDLIB is None:
            self.skipTest("no zstd fixture encoder available")
        if known:
            return STDLIB.compress(payload)
        compressor = STDLIB.ZstdCompressor()
        return compressor.compress(payload) + compressor.flush()

    def check_output_limit(self, backend):
        self.require_backend(backend)
        cap = USAGE._OPENCLAW_MAX_EVENT_BYTES
        for known in (True, False):
            with self.subTest(backend=backend, known_size=known):
                exact = b"x" * cap
                compressed = self.encode(exact, known)
                with self.backend(backend):
                    self.assertEqual(USAGE._openclaw_zstd_blob(compressed), exact)
                oversized = self.encode(b"x" * (cap + 1), known)
                with self.backend(backend):
                    self.assertTrue(USAGE._openclaw_zstd_blob(oversized) is None,
                                    "oversized frame was accepted")

    def test_stdlib_known_and_unknown_size_output_limit(self):
        self.check_output_limit("stdlib")

    def test_optional_known_and_unknown_size_output_limit(self):
        self.check_output_limit("optional")

    def check_window_limit(self, backend):
        self.require_backend(backend)
        payload = b'{"synthetic":true}'
        frame = bytearray(self.encode(payload, False))
        self.assertFalse(frame[4] & 0x20)  # non-single-segment: byte 5 is window descriptor
        frame[5] = (25 - 10) << 3  # valid 32-MiB window, tiny actual output
        frame = bytes(frame)
        if OPTIONAL is not None:
            self.assertEqual(OPTIONAL.get_frame_parameters(frame).window_size,
                             32 * 1024 * 1024)
            with OPTIONAL.ZstdDecompressor().stream_reader(io.BytesIO(frame)) as reader:
                self.assertEqual(reader.read(), payload)
        with self.backend(backend):
            self.assertIsNone(USAGE._openclaw_zstd_blob(frame))

    def test_stdlib_rejects_window_abuse(self):
        self.check_window_limit("stdlib")

    def test_optional_rejects_window_abuse(self):
        self.check_window_limit("optional")

    def check_invalid_frames(self, backend):
        self.require_backend(backend)
        for known in (True, False):
            good = self.encode(b'{"synthetic":true}', known)
            for label, frame in (
                    ("truncated", good[:-1]),
                    ("trailing", good + b"garbage"),
                    ("extra-frame", good + self.encode(b"extra", known)),
                    ("oversized-extra-frame", good + self.encode(
                        b"x" * (USAGE._OPENCLAW_MAX_EVENT_BYTES + 1), known))):
                with self.subTest(backend=backend, known_size=known, case=label):
                    with self.backend(backend):
                        self.assertIsNone(USAGE._openclaw_zstd_blob(frame))

    def test_stdlib_rejects_incomplete_or_trailing_frames(self):
        self.check_invalid_frames("stdlib")

    def test_optional_rejects_incomplete_or_trailing_frames(self):
        self.check_invalid_frames("optional")

    def test_stdlib_is_preferred_without_importing_optional_package(self):
        frame = self.encode(b"small event", True)
        with self.backend("stdlib"):
            self.assertTrue(USAGE._openclaw_zstd_available())
            self.assertEqual(USAGE._openclaw_zstd_blob(frame), b"small event")

    def test_actual_optional_backend_reads_sqlite_usage(self):
        if OPTIONAL is None:
            self.skipTest("optional zstandard unavailable")
        now = datetime.now().astimezone()
        event = {
            "type": "message", "id": "real-module-event",
            "timestamp": now.isoformat(),
            "message": {"role": "assistant", "model": "vendor/test",
                        "usage": {"input": 7, "output": 2, "totalTokens": 9,
                                  "cost": {"total": 0.1}}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.sqlite"
            with contextlib.closing(sqlite3.connect(path)) as connection:
                connection.execute("""
                    CREATE TABLE transcript_events (
                        session_id TEXT, seq INTEGER, event_json TEXT,
                        created_at INTEGER, event_zstd BLOB
                    )
                """)
                connection.execute(
                    "INSERT INTO transcript_events VALUES (?, ?, NULL, ?, ?)",
                    ("real-module-session", 1, int(now.timestamp() * 1000),
                     OPTIONAL.ZstdCompressor(write_content_size=False).compress(
                         json.dumps(event).encode())),
                )
                connection.commit()
            with self.backend("optional"):
                sessions = USAGE._scan_openclaw_agent_db(str(path), sqlite3)
        day = sessions["real-module-session"]["days"][now.date().isoformat()]
        self.assertEqual((day["in"], day["out"]), (7, 2))


if __name__ == "__main__":
    unittest.main()

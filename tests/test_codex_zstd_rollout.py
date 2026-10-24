"""官方 Codex 分页存储迁移（legacy_to_paginated_v1）会把旧 rollout 压缩为
rollout-*.jsonl.zst；采集器必须仍然统计这些会话的用量。"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_codex_limits import USAGE


def _zstd_compress(data):
    try:
        from compression import zstd  # Python 3.14+
        return zstd.compress(data)
    except Exception:
        pass
    try:
        import zstandard
        return zstandard.ZstdCompressor().compress(data)
    except Exception:
        return None


def _record(ts, **fields):
    record = {"timestamp": ts}
    record.update(fields)
    return json.dumps(record, ensure_ascii=False)


def _token_count(ts, total, last):
    keys = ("input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_output_tokens")
    return _record(ts, type="event_msg", payload={
        "type": "token_count",
        "info": {
            "total_token_usage": dict(zip(keys, total)),
            "last_token_usage": dict(zip(keys, last)),
        },
    })


class CodexZstdRolloutTests(unittest.TestCase):
    def test_scan_counts_officially_compacted_rollout(self):
        payload = "\n".join([
            _record("2024-01-08T00:00:00Z", ordinal=0, type="session_meta",
                    payload={"id": "zst-session", "session_id": "zst-session",
                             "timestamp": "2024-01-08T00:00:00Z",
                             "cwd": "/tmp/project", "originator": "Codex Desktop"}),
            _record("2024-01-08T00:00:30Z", type="turn_context",
                    payload={"model": "gpt-5.5", "cwd": "/tmp/project"}),
            _token_count("2024-01-08T00:01:00Z", (120, 0, 10, 2), (120, 0, 10, 2)),
            _token_count("2024-01-08T00:02:00Z", (300, 0, 25, 5), (180, 0, 15, 3)),
        ]) + "\n"
        compressed = _zstd_compress(payload.encode("utf-8"))
        if compressed is None:
            self.skipTest("no zstd encoder available")

        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2024" / "01" / "08"
            day.mkdir(parents=True)
            (day / "rollout-2024-01-08T00-00-00-zst-session.jsonl.zst").write_bytes(compressed)
            day_utc = datetime(2024, 1, 8, tzinfo=timezone.utc)
            with mock.patch.object(USAGE, "CODEX_DIR", tmp), \
                 mock.patch.object(USAGE, "CODEX_ARCHIVED_DIR", str(Path(tmp) / "archived_sessions")), \
                 mock.patch.object(USAGE, "fetch_codex_live_limits", return_value=None):
                result = USAGE.scan_codex({
                    "today": day_utc,
                    "yesterday": datetime(2024, 1, 7, tzinfo=timezone.utc),
                    "week": day_utc,
                    "last_week": datetime(2024, 1, 1, tzinfo=timezone.utc),
                    "last_week_end": day_utc,
                    "month": datetime(2024, 1, 1, tzinfo=timezone.utc),
                    "year": datetime(2024, 1, 1, tzinfo=timezone.utc),
                }, {"v": USAGE._SCAN_CACHE_VERSION})

        usage = result["ranges"]["all"]
        self.assertEqual(usage["in"], 300)
        self.assertEqual(usage["out"], 25)
        self.assertEqual(len(usage["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()

"""Request timing, weighted rates and durable upgrades for Pi-compatible sessions."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_codex_limits import USAGE as U


class PiRateAccuracyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tokei-pi-rate-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.started = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
        for patcher in (
            mock.patch.object(U, "_pi_session_dirs", return_value=[str(self.sessions)]),
            mock.patch.object(U, "_LEDGER_FILE", str(self.root / "ledger.json")),
            mock.patch.object(U, "_LEDGER_TODAY_FLUSH_INTERVAL", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: U._LEDGER_CACHE.update(data=None, dirty=False, urgent=False))

    def record(self, *, output=600, elapsed=30, **timing):
        return {
            "type": "message",
            "timestamp": (self.started + timedelta(seconds=elapsed)).isoformat(),
            "message": {
                "role": "assistant", "provider": "fixture", "model": "m",
                "timestamp": int(self.started.timestamp() * 1000),
                "usage": {"input": 100, "output": output}, **timing,
            },
        }

    def write(self, *records):
        path = self.sessions / "s.jsonl"
        rows = [{"type": "session", "id": "s", "cwd": "/fixture"}, *records]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    def scan(self, cache=None):
        result = U.scan_pi(U.range_bounds(), {} if cache is None else cache)["ranges"]["all"]
        return U._perf_summary(result.get("perf")), result

    def test_standard_pi_rate_is_end_to_end_and_has_no_invented_ttft(self):
        self.write(self.record(elapsed=30))
        perf, result = self.scan()
        self.assertEqual((perf["o"], perf["g"], perf["tps"], perf["en"]), (600, 30, 20, 1))
        self.assertIsNone(perf["ttft"])
        self.assertEqual(U.token_total(result), 700)

    def test_recorded_timing_excludes_wait_and_delayed_persistence(self):
        self.write(self.record(elapsed=45, duration=12000, ttft=2000))
        perf, _ = self.scan()
        self.assertEqual((perf["g"], perf["tps"], perf["en"], perf["tn"]), (10, 60, 0, 1))
        self.assertAlmostEqual(perf["ttft"], 2, delta=0.05)

    def test_recorded_duration_without_first_token_time_still_includes_wait(self):
        self.write(self.record(elapsed=45, duration=10000))
        perf, _ = self.scan()
        self.assertEqual((perf["g"], perf["tps"], perf["en"]), (10, 60, 1))
        self.assertIsNone(perf["ttft"])

    def test_long_first_token_wait_does_not_reduce_generation_speed(self):
        self.write(self.record(elapsed=240, duration=210000, ttft=200000))
        perf, _ = self.scan()
        self.assertEqual((perf["g"], perf["tps"], perf["en"]), (10, 60, 0))
        self.assertEqual(perf["tn"], 0, "Outlier TTFT is filtered separately from generation timing")

    def test_invalid_recorded_timing_falls_back_without_inflating_rate(self):
        for duration, ttft in [(None, 2000), (True, 0), (-1, 0), (float("nan"), 0), (float("inf"), 0)]:
            with self.subTest(duration=duration, ttft=ttft):
                timing = U._pi_request_timing(self.record(), self.record(duration=duration, ttft=ttft)["message"])
                self.assertEqual(timing, (30, None, True))
        for ttft in (12000, 15000, -1, True, float("inf")):
            timing = U._pi_request_timing(self.record(), self.record(duration=12000, ttft=ttft)["message"])
            self.assertEqual(timing, (12, None, True))

    def test_weighted_rate_and_wait_sample_count_survive_flush_and_restart(self):
        self.write(self.record(duration=12000, ttft=2000), self.record(output=400, elapsed=40))
        perf, _ = self.scan()
        self.assertEqual((perf["o"], perf["g"], perf["tps"], perf["n"], perf["en"]), (1000, 50, 20, 2, 1))
        U.ledger_flush()
        U._LEDGER_CACHE.update(data=None, dirty=False, urgent=False)
        after, _ = self.scan()
        self.assertEqual(after, perf)
        stored = next(iter(U._load_ledger()["tools"]["pi"].values()))
        self.assertEqual(stored["perf"]["fixture/m"]["en"], 1)

    def test_parser_upgrade_replaces_old_duration_in_cache_and_ledger(self):
        path = self.write(self.record(elapsed=45, duration=12000, ttft=2000))
        day_key = self.started.astimezone().date().isoformat()
        old_day = {"in": 100, "out": 600, "cr": 0, "cw": 0, "reason": 0, "cost": 0,
                   "perf": {"fixture/m": {"o": 600, "g": 45, "n": 1}},
                   "models": {"fixture/m": {"in": 100, "out": 600}}, "hours": [0] * 24}
        stat = path.stat()
        old_files = {str(path): {"sig": f"{stat.st_mtime}:{stat.st_size}", "parser": 5,
                                 "sid": "s", "days": {day_key: copy.deepcopy(old_day)}}}
        U.ledger_reconcile("pi", {day_key: old_day}, U._ledger_file_sources(old_files, "sid"))
        U.ledger_flush()
        U._LEDGER_CACHE.update(data=None, dirty=False, urgent=False)
        perf, result = self.scan({"pi": old_files})
        self.assertEqual((perf["tps"], perf["g"], perf["n"]), (60, 10, 1))
        self.assertEqual(U.token_total(result), 700)
        U.ledger_flush()
        U._LEDGER_CACHE.update(data=None, dirty=False, urgent=False)
        self.assertEqual(self.scan()[0], perf)


if __name__ == "__main__":
    unittest.main()

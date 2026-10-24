"""Native quota-detail regressions; all input and home/cache state is synthetic."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "Native Swift model tests require macOS")
class QuotaDetailModelTests(unittest.TestCase):
    def test_cost_compatibility_reset_jitter_and_fixture_isolation(self):
        with tempfile.TemporaryDirectory(prefix="tokei-quota-detail-") as directory:
            tmp = Path(directory)
            home = tmp / "home"
            cache = home / ".tokei"
            cache.mkdir(parents=True)
            cycle = {
                "tool": "claude", "start": 900, "end": 1100,
                "used_pct": 25, "tokens": 100, "cost": 12.5,
                "devices": {"synthetic": 100}, "approx": True, "current": True,
            }
            payload = {
                "daily": [], "cycles": [cycle], "devices": ["synthetic"],
                "missing": [], "now": 1000,
            }
            fixture = tmp / "quota-fixture.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            live_cache = cache / "quota_detail.json"
            canary = dict(payload, cycles=[dict(cycle, cost=9999.99)])
            live_cache.write_text(json.dumps(canary), encoding="utf-8")
            before = (fixture.read_bytes(), live_cache.read_bytes())
            for name in ("tmp", "module-cache"):
                (tmp / name).mkdir()
            env = {
                key: os.environ[key]
                for key in ("PATH", "DEVELOPER_DIR", "SDKROOT", "LANG")
                if key in os.environ
            }
            env.update(
                HOME=str(home), CFFIXED_USER_HOME=str(home), TMPDIR=str(tmp / "tmp"),
                CLANG_MODULE_CACHE_PATH=str(tmp / "module-cache"),
                SWIFTPM_MODULECACHE_OVERRIDE=str(tmp / "module-cache"),
            )
            binary = tmp / "quota-detail-check"
            compile_result = subprocess.run(
                ["swiftc", "-parse-as-library", "-framework", "Combine",
                 str(ROOT / "Tokei/Sources/Tokei/QuotaDetail.swift"),
                 str(ROOT / "tests/swift/QuotaDetailCheck.swift"), "-o", str(binary)],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(
                compile_result.returncode, 0, compile_result.stdout + compile_result.stderr
            )
            for mode in ("fixture", "no-fixture"):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        [str(binary), str(fixture), mode], cwd=tmp, env=env,
                        capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("quota detail checks passed", result.stdout)
                    self.assertEqual(
                        (fixture.read_bytes(), live_cache.read_bytes()), before,
                        "Neither fixture mode may write the fixture or synthetic live cache",
                    )


if __name__ == "__main__":
    unittest.main()

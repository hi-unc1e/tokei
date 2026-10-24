import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "macOS Swift/Combine integration")
class CodexResetAnnouncementTests(unittest.TestCase):
    def test_feed_timing_and_network_cache(self):
        self.run_swift_check("CodexResetAnnouncementCheck.swift")

    def test_network_security_controls(self):
        self.run_swift_check("CodexResetNetworkSecurityCheck.swift", ("sizes", "redirects"))

    def run_swift_check(self, check, groups=(None,)):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "reset-check"
            source = ROOT / "Tokei/Sources/Tokei"
            result = subprocess.run([
                "swiftc", "-parse-as-library",
                str(source / "CodexResetFeed.swift"),
                str(source / "CodexResetMonitor.swift"),
                str(ROOT / "tests/swift" / check),
                "-o", str(binary),
            ], capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(["codesign", "--force", "--sign", "-", str(binary)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            for group in groups:
                with self.subTest(group=group):
                    args = [str(binary), temp] + ([group] if group else [])
                    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

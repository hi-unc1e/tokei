"""Run the production Windows card/share regressions in the full Python suite."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowsFrontendRegressionTests(unittest.TestCase):
    def test_card_and_share_regressions(self):
        node = shutil.which("node")
        if not node or not (ROOT / "windows/node_modules/vite/package.json").is_file():
            self.skipTest("Windows frontend dependencies are not installed")
        result = subprocess.run(
            [node, "--test"], cwd=ROOT / "windows",
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

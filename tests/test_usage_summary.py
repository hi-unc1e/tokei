import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEI_SRC = ROOT / "Tokei" / "Sources" / "Tokei"


class UsageSummaryBuilderTests(unittest.TestCase):
    def test_summary_builder_period_visibility_and_totals(self):
        # GitHub Actions 的 macOS runner 无 GUI 会话，ImageRenderer/剪贴板段会
        # SIGSEGV；置 TOKEI_HEADLESS=1 让该段跳过，纯逻辑断言全部保留。
        env = dict(os.environ)
        if env.get("GITHUB_ACTIONS") == "true":
            env["TOKEI_HEADLESS"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "usage-summary-check"
            subprocess.run(
                [
                    "swiftc",
                    "-parse-as-library",
                    str(TOKEI_SRC / "Model.swift"),
                    str(TOKEI_SRC / "Design.swift"),
                    str(TOKEI_SRC / "PanelTypography.swift"),
                    str(TOKEI_SRC / "UsageSummaryBuilder.swift"),
                    str(TOKEI_SRC / "UsageShareImage.swift"),
                    str(ROOT / "tests/swift/UsageSummaryBuilderCheck.swift"),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("usage summary builder checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

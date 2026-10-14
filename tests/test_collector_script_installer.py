import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CollectorScriptInstallerTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Tokei installer is macOS-only")
    def test_swift_collector_installer_policy(self):
        swiftc = shutil.which("swiftc")
        self.assertIsNotNone(swiftc, "swiftc is required")

        root = Path(__file__).resolve().parents[1]
        security = root / "Tokei/Sources/TokeiUpdateSecurity/UpdateSecurity.swift"
        installer = root / "Tokei/Sources/TokeiUpdateSecurity/CollectorScriptInstaller.swift"
        harness = root / "tests/swift/CollectorScriptInstallerCheck.swift"

        with tempfile.TemporaryDirectory(prefix="tokei-collector-installer-test-") as temp_dir:
            binary = Path(temp_dir) / "collector-installer-check"
            environment = os.environ.copy()
            environment["CLANG_MODULE_CACHE_PATH"] = str(Path(temp_dir) / "clang-cache")
            environment["SWIFT_MODULECACHE_PATH"] = str(Path(temp_dir) / "swift-cache")
            compile_result = subprocess.run(
                [swiftc, str(security), str(installer), str(harness), "-o", str(binary)],
                capture_output=True,
                text=True,
                # 模块缓存指向空的临时目录，每次都要从头构建系统框架的缓存：
                # 新版 SDK 上实测约 63 秒，原来的 60 秒上限会被它单独吃满。
                timeout=300,
                env=environment,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )

            run_result = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                run_result.returncode,
                0,
                run_result.stdout + run_result.stderr,
            )
            self.assertIn("Collector script installer checks passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()

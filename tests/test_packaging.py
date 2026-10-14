"""打包出的是通用二进制，Intel Mac 也能直接装（issue #13）。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UniversalBuildTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "Tokei" / "package.sh").read_text(encoding="utf-8")

    def test_both_architectures_are_built_by_default(self):
        default = re.search(r'ARCHS="\$\{TOKEI_ARCHS:-([^}]*)\}"', self.script)
        self.assertIsNotNone(default)
        self.assertEqual(default.group(1).split(), ["arm64", "x86_64"])
        self.assertIn('swift build -c release --arch "$arch"', self.script)

    def test_every_shipped_binary_is_merged_and_verified(self):
        self.assertIn('merge_arches Tokei "$APP/Contents/MacOS/Tokei"', self.script)
        self.assertIn('merge_arches TokeiGrokBotHelper "$APP/Contents/Helpers/TokeiGrokBotHelper"',
                      self.script)
        self.assertIn("lipo -create", self.script)
        self.assertIn('-verify_arch "$arch"', self.script)
        # 不能再有直接拷单架构产物的旧写法
        self.assertNotIn('cp "$BIN"', self.script)


if __name__ == "__main__":
    unittest.main()

"""Run only the decoder-probe block with a non-installing Python test stub."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BASH = shutil.which("bash")


@unittest.skipUnless(os.name == "posix" and BASH,
                     "The POSIX collector installer requires bash; Windows uses the native installer")
class OpenClawInstallerProbeTests(unittest.TestCase):
    def run_probe(self, exit_code):
        script = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        start = script.index("# 4.")
        end = script.index("# 5.", start)
        block = script[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "python3"
            log = root / "calls.jsonl"
            executable.write_text(
                "#!" + sys.executable + "\n"
                "import json,os,sys\n"
                "with open(os.environ['PROBE_LOG'],'a') as f: "
                "f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                "raise SystemExit(int(os.environ['PROBE_EXIT']))\n")
            executable.chmod(0o700)
            environment = os.environ.copy()
            environment.update(PATH=str(root), PROBE_LOG=str(log),
                               PROBE_EXIT=str(exit_code), TOKEI_DIR=str(root / "tokei"))
            result = subprocess.run(
                [BASH, "-c", block], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1, "probe must never launch pip installation")
        self.assertNotIn("--break-system-packages", block)
        return result.stdout

    def test_available_decoder_does_not_install(self):
        output = self.run_probe(0)
        self.assertIn("已可用", output)

    def test_missing_decoder_prints_manual_isolated_hint_without_installing(self):
        output = self.run_probe(1)
        self.assertIn("-m venv", output)
        self.assertIn("venv/bin/python", output)
        self.assertIn("暂时跳过", output)


if __name__ == "__main__":
    unittest.main()

"""Cross-platform checks for time-range sorting and token accounting pitfalls."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/usage_card_sorting.json"
SRC = ROOT / "Tokei/Sources/Tokei"


def normalized_fixture():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    usage = fixture["usage"]
    empty = dict(hit=0, tokens=0, **{"in": 0}, out=0, cr=0, cw=0, cached=0,
                 reason=0, thoughts=0, cost=0, credits=0, requests=0,
                 sessions=0, tasks=0, completed=0, failed=0, models=[])
    keys = ("today", "yesterday", "week", "last_week", "month", "year")

    def fill(stat):
        for name in ("ranges", "reserve_ranges"):
            if name in stat:
                ranges = stat[name]
                for key in keys:
                    ranges[key] = {**copy.deepcopy(empty), **ranges.get(key, {})}
        for name in ("usage", "quota"):
            if name in stat:
                fill(stat[name])

    for provider in ("claude", "codex", "gemini", "grok", "hermes", "openclaw", "opencode"):
        usage.setdefault(provider, {"ranges": {}})
    usage["claude"].update(session_name="", session_total=0)
    for stat in usage.values():
        fill(stat)
    return fixture


class UsageCardSortingTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Swift app is macOS-only")
    def test_native_card_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "card-sort-check"
            fixture = Path(tmp) / "fixture.json"
            fixture.write_text(json.dumps(normalized_fixture()), encoding="utf-8")
            result = subprocess.run([
                "swiftc", "-parse-as-library", str(SRC / "Model.swift"),
                str(SRC / "L10n.swift"), str(SRC / "UsageCardOrdering.swift"),
                str(ROOT / "tests/swift/UsageCardOrderingCheck.swift"), "-o", str(binary),
            ], capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary), str(fixture)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage card ordering checks passed", result.stdout)

    def test_windows_card_ordering(self):
        node = shutil.which("node")
        compiler = ROOT / "windows/node_modules/typescript/bin/tsc"
        if not node or not compiler.is_file():
            self.skipTest("Windows frontend dependencies are not installed")
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([
                node, str(compiler), "--ignoreConfig", "--module", "commonjs",
                "--target", "ES2020", "--skipLibCheck", "--outDir", tmp,
                "src/lib/cardOrder.ts",
            ], cwd=ROOT / "windows", capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {cardTokens, sortedUsageCards} = require(process.argv[1]);
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
for (const test of fixture.cases) {
  assert.equal(cardTokens(test.id, fixture.usage, test.range), test.tokens, `${test.id}/${test.range}`);
}
for (const test of fixture.orders) {
  const cards = Object.freeze(test.ids.map(id => Object.freeze({id})));
  assert.deepEqual(sortedUsageCards(cards, fixture.usage, test.range).map(card => card.id), test.expected);
}
assert.equal(cardTokens('cursor', {}, 'today'), 0);
console.log('usage card ordering checks passed');
"""
            result = subprocess.run([
                node, "-e", script, str(Path(tmp) / "cardOrder.js"), str(FIXTURE),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage card ordering checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

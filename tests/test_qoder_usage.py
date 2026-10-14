import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

try:
    from .test_codex_limits import USAGE
except ImportError:
    from test_codex_limits import USAGE


class QoderUsageTests(unittest.TestCase):
    def write_jsonl(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

    def assistant(self, timestamp, message_id, request_id, *, model="ultimate",
                  input_tokens=0, output_tokens=0, cache_read=0, cache_write=0,
                  credits=0.0):
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "message": {
                "id": message_id,
                "model": model,
                "content": [{"type": "text", "text": "response"}],
                "usage": {
                    "request_id": request_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                    "credits": credits,
                },
            },
        }

    def scan_cli(self, root):
        cache = {"v": USAGE._SCAN_CACHE_VERSION}
        with mock.patch.dict(os.environ, {"TOKEI_QODERCLI_DIR": str(root)}), \
             mock.patch.object(USAGE, "ledger_touch"), \
             mock.patch.object(USAGE, "ledger_reconcile", side_effect=lambda _tool, days: days):
            return USAGE.scan_qodercli(USAGE.range_bounds(), cache), cache

    def test_qodercli_exact_tokens_are_normalized_and_deduped_across_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            now = datetime.now().astimezone().replace(microsecond=0).isoformat()
            first = self.assistant(
                now, "message-shared", "request-1",
                input_tokens=100, output_tokens=20, cache_read=30, cache_write=10,
                credits=1.5,
            )
            second = self.assistant(
                now, "message-shared", "request-2",
                input_tokens=50, output_tokens=8, cache_read=20, credits=0.4,
            )
            zero_token = self.assistant(
                now, "message-zero", "request-zero", credits=2.5,
            )
            child = self.assistant(
                now, "message-child", "request-child",
                input_tokens=40, output_tokens=7, cache_read=10, cache_write=5,
                credits=0.8,
            )
            self.write_jsonl(
                root / "project" / "session.jsonl",
                [{"type": "runtime-config", "model": "auto"}, first, second, zero_token],
            )
            self.write_jsonl(
                root / "project" / "session" / "subagents" / "child.jsonl",
                [first, child],
            )

            result, _cache = self.scan_cli(root)

        usage = result["ranges"]["all"]
        self.assertEqual(usage["in"], 115)
        self.assertEqual(usage["cr"], 60)
        self.assertEqual(usage["cw"], 15)
        self.assertEqual(usage["out"], 35)
        self.assertEqual(usage["calls"], 4)
        self.assertEqual(usage["usage_calls"], 3)
        self.assertTrue(usage["usage_available"])
        self.assertAlmostEqual(usage["credits"], 5.2)
        self.assertEqual(len(_cache["qodercli"]["_requests"]), 4)
        self.assertIn("_usage_days", _cache["qodercli"])
        for path, entry in _cache["qodercli"].items():
            if not path.startswith("_"):
                self.assertNotIn("responses", entry)

    def test_qoder_cn_reads_its_own_directory_and_cache_key(self):
        """国内版 ~/.qoder-cn 单独成键 qodercli_cn：目录、缓存、账本都不和国际版串。"""
        with tempfile.TemporaryDirectory() as tmp:
            intl = Path(tmp) / "intl" / "projects"
            cn = Path(tmp) / "cn" / "projects"
            now = datetime.now().astimezone().replace(microsecond=0).isoformat()
            self.write_jsonl(intl / "project" / "session.jsonl", [
                self.assistant(now, "m-intl", "r-intl", input_tokens=100, output_tokens=5),
            ])
            # 国内版内置路由不落 token（usage 全 0），只有 Credits
            self.write_jsonl(cn / "project" / "session.jsonl", [
                self.assistant(now, "m-cn-1", "r-cn-1", model="qfmodel", credits=2.0),
                self.assistant(now, "m-cn-2", "r-cn-2", model="qfmodel", credits=1.5),
            ])
            cache = {"v": USAGE._SCAN_CACHE_VERSION}
            reconciled = []

            def reconcile(tool, days):
                reconciled.append(tool)
                return days

            with mock.patch.dict(os.environ, {"TOKEI_QODERCLI_DIR": str(intl),
                                              "TOKEI_QODERCLI_CN_DIR": str(cn)}), \
                 mock.patch.object(USAGE, "ledger_touch"), \
                 mock.patch.object(USAGE, "ledger_reconcile", side_effect=reconcile):
                bounds = USAGE.range_bounds()
                intl_result = USAGE.scan_qodercli(bounds, cache)
                cn_result = USAGE.scan_qodercli(bounds, cache, tool="qodercli_cn")

        intl_usage = intl_result["ranges"]["all"]
        cn_usage = cn_result["ranges"]["all"]
        self.assertEqual((intl_usage["calls"], intl_usage["in"]), (1, 100))
        self.assertEqual(cn_usage["calls"], 2)
        self.assertEqual(cn_usage["in"] + cn_usage["out"], 0)
        self.assertFalse(cn_usage["usage_available"])
        self.assertAlmostEqual(cn_usage["credits"], 3.5)
        self.assertEqual(cn_result["model"], "qfmodel")
        self.assertEqual(reconciled, ["qodercli", "qodercli_cn"])
        self.assertTrue(all(str(intl) in path for path in cache["qodercli"]
                            if not path.startswith("_")))
        self.assertTrue(all(str(cn) in path for path in cache["qodercli_cn"]
                            if not path.startswith("_")))

    def test_qoder_cn_ledger_schema_is_independent(self):
        """国内版清账本只清自己，国际版已有的账本和 schema 不受影响。"""
        stored = {
            "v": USAGE._LEDGER_VERSION,
            "qodercli_schema": USAGE._QODERCLI_LEDGER_VERSION,
            "tools": {"qodercli": {"2026-01-01": {"calls": 3}},
                      "qodercli_cn": {"2026-01-01": {"calls": 9, "est": 999}}},
        }
        saved = {}
        USAGE._LEDGER_CACHE["data"] = json.loads(json.dumps(stored))
        USAGE._LEDGER_CACHE["dirty"] = False
        try:
            with mock.patch.object(USAGE, "_load_ledger_from_disk",
                                   return_value=json.loads(json.dumps(stored))), \
                 mock.patch.object(USAGE, "_save_ledger",
                                   side_effect=lambda value: saved.update(value)):
                USAGE._prepare_qodercli_ledger()
                USAGE._prepare_qodercli_ledger("qodercli_cn")
                USAGE.ledger_flush()
        finally:
            USAGE._LEDGER_CACHE["data"] = None
            USAGE._LEDGER_CACHE["dirty"] = False

        self.assertEqual(saved["tools"]["qodercli"], {"2026-01-01": {"calls": 3}})
        self.assertEqual(saved["tools"]["qodercli_cn"], {})
        self.assertEqual(saved["qodercli_cn_schema"], USAGE._QODERCLI_LEDGER_VERSION)

    def test_legacy_qodercli_ledger_is_cleared_before_exact_usage(self):
        ledger = {
            "v": USAGE._LEDGER_VERSION,
            "tools": {"qodercli": {"2026-01-01": {"calls": 9, "est": 999}}},
        }
        saved = {}
        USAGE._LEDGER_CACHE["data"] = ledger
        USAGE._LEDGER_CACHE["dirty"] = False
        try:
            with mock.patch.object(USAGE, "_load_ledger_from_disk", return_value={
                    "v": USAGE._LEDGER_VERSION,
                    "tools": {"qodercli": {"2026-01-01": {"calls": 9, "est": 999}}},
                 }), mock.patch.object(USAGE, "_save_ledger", side_effect=lambda value: saved.update(value)):
                USAGE._prepare_qodercli_ledger()
                USAGE.ledger_flush()
        finally:
            USAGE._LEDGER_CACHE["data"] = None
            USAGE._LEDGER_CACHE["dirty"] = False

        self.assertEqual(saved["tools"]["qodercli"], {})
        self.assertEqual(saved["qodercli_schema"], USAGE._QODERCLI_LEDGER_VERSION)

    def test_streaming_response_is_not_double_counted_when_request_id_arrives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            now = datetime.now().astimezone().replace(microsecond=0).isoformat()
            partial = {
                "type": "assistant", "timestamp": now,
                "message": {"id": "message-1", "model": "ultimate", "content": []},
            }
            final = self.assistant(
                now, "message-1", "request-1", input_tokens=10, output_tokens=2,
            )
            self.write_jsonl(root / "project" / "session.jsonl", [partial, final])

            result, _cache = self.scan_cli(root)

        usage = result["ranges"]["all"]
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["in"] + usage["out"], 12)

    def test_duplicate_request_keeps_richer_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            now = datetime.now().astimezone().replace(microsecond=0).isoformat()
            rich = self.assistant(
                now, "message-1", "request-1", input_tokens=10, output_tokens=2,
                credits=3.5,
            )
            rich["message"]["content"] = [{"type": "tool_use", "id": "tool-1", "input": {}}]
            sparse = self.assistant(
                now, "message-1", "request-1", input_tokens=10, output_tokens=2,
            )
            self.write_jsonl(root / "project" / "session.jsonl", [rich])
            self.write_jsonl(root / "project" / "session" / "subagents" / "child.jsonl", [sparse])

            result, _cache = self.scan_cli(root)

        usage = result["ranges"]["all"]
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["tools"], 1)
        self.assertEqual(usage["credits"], 3.5)

    def test_qodercli_excludes_legacy_desktop_transcript_mirrors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            now = datetime.now().astimezone().replace(microsecond=0).isoformat()
            self.write_jsonl(
                root / "project" / "session.jsonl",
                [self.assistant(now, "cli-message", "cli-request", input_tokens=10, output_tokens=2)],
            )
            self.write_jsonl(
                root / "project" / "transcript" / "desktop-mirror.jsonl",
                [self.assistant(now, "desktop-message", "desktop-request",
                                input_tokens=900, output_tokens=100)],
            )

            result, cache = self.scan_cli(root)

        usage = result["ranges"]["all"]
        self.assertEqual(usage["in"], 10)
        self.assertEqual(usage["out"], 2)
        self.assertEqual(usage["calls"], 1)
        cached_paths = [key for key in cache["qodercli"] if not key.startswith("_")]
        self.assertFalse(any(f"{os.sep}transcript{os.sep}" in path for path in cached_paths))


if __name__ == "__main__":
    unittest.main()

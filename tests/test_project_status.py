import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from test_codex_limits import USAGE

STATUS = """---
hq: 1
project: demo
theme: 游戏
value: 好玩
state: active
updated: 2026-09-26 09:00
---
# demo · 状态

> 联机房间号**已上线**。

## ❓ 待 Henry 判断
- [ ] **真机试听** — 怎么验：… — 建议：有意见写 `→ Henry: …`
- [x] 已看过的截图
- [ ] 水雾浓度 → Henry: 再淡一点

## ⛔ 阻塞
- 无

## ▶ 下一步（agent 自主推进，无需回复）
- 补 e2e

## ✅ 机器验收
<!-- hq:verify:start -->
- [ ] 机器区里的列表不算
<!-- hq:verify:end -->
"""


def rollout(path, cwd, source="vscode"):
    meta = {"type": "session_meta", "payload": {"id": path.stem, "cwd": cwd, "source": source}}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")


def codex_entry(day, cost, tokens):
    return {"days": {day: {"in": tokens, "out": 0, "reason": 0, "cost": cost,
                           "models": {"openai/gpt-5.5": {"in": tokens, "out": 0, "cr": 0, "cw": 0,
                                                         "reason": 0, "cost": cost}}}}}


def run_projects(cache):
    out = io.StringIO()
    with mock.patch.object(USAGE, "compute"), \
            mock.patch.object(USAGE, "_load_scan_cache", return_value=cache), \
            mock.patch.object(USAGE, "_detect_local_servers", return_value={}), \
            contextlib.redirect_stdout(out):
        USAGE.projects()
    return json.loads(out.getvalue())


class HqStatusParsingTests(unittest.TestCase):
    def test_parses_sections_and_ignores_machine_block(self):
        st = USAGE._hq_parse_status(STATUS)
        self.assertEqual(st["theme"], "游戏")
        self.assertEqual(st["summary"], "联机房间号已上线。")
        self.assertEqual(st["needs_you"], ["真机试听"])  # 示例批注不算答复；** 去掉；只保留标题
        self.assertEqual(st["answered"], 2)
        self.assertEqual(st["blocked"], [])
        self.assertEqual(st["next"], ["补 e2e"])

    def test_non_protocol_markdown_is_ignored(self):
        self.assertIsNone(USAGE._hq_parse_status("# 普通 README\n"))
        self.assertIsNone(USAGE._hq_parse_status("---\ntitle: x\n---\n"))

    def test_status_includes_verify_and_recent_gate_give_ups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "STATUS.md").write_text(STATUS, encoding="utf-8")
            (root / ".hq").mkdir()
            (root / ".hq" / "verify.json").write_text(json.dumps({"checks": {
                "unit": {"ok": True, "at": "2026-09-26T09:00:00"},
                "e2e": {"ok": False, "at": "2026-09-26T09:10:00"},
                "codex": {"ok": False, "not_run": True, "at": "2026-09-26T09:05:00"},
            }}), encoding="utf-8")
            now = datetime.now().isoformat(timespec="seconds")
            (root / ".hq" / "gate.log").write_text(
                f"2020-01-01T00:00:00\tgave-up\told\n{now}\tgave-up\ts\n{now}\tpass\tfp\n", encoding="utf-8")
            st = USAGE.hq_status(str(root))
        self.assertEqual((st["verify_passed"], st["verify_total"]), (1, 3))
        self.assertEqual(st["verify_failed"], ["e2e"])
        self.assertEqual(st["verify_at"], "2026-09-26T09:10:00")
        self.assertEqual(st["gate_gave_up_24h"], 1)


class CodexProjectsTests(unittest.TestCase):
    def test_codex_sessions_attribute_cost_to_cwd_and_skip_subagent_session_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            proj = tmp / "proj"
            proj.mkdir()
            (proj / "STATUS.md").write_text(STATUS, encoding="utf-8")
            main, guardian, fork = tmp / "a.jsonl", tmp / "b.jsonl", tmp / "c.jsonl"
            rollout(main, str(proj))
            rollout(guardian, str(proj), source={"subagent": {"other": "guardian"}})
            rollout(fork, str(proj))
            cache = {"codex": {
                # 扫描阶段已把 cwd 回填进缓存（_codex_session_cwd_info），
                # 子代理会话同时落 proj_nosession 标记
                str(main): {**codex_entry("2026-09-25", 2.0, 1000), "proj": str(proj)},
                str(guardian): {**codex_entry("2026-09-26", 0.5, 200),
                                "proj": str(proj), "proj_nosession": True},
                str(fork): {"days": {}, "proj": str(proj)},  # 非 canonical 的 fork：用量已并入原会话
                str(tmp / "missing.jsonl"): codex_entry("2026-09-26", 9.0, 9),
            }}
            rows = run_projects(cache)
        row = next(r for r in rows if r["path"] == str(proj))
        self.assertEqual(row["tools"], ["codex"])
        self.assertEqual(row["sessions"], 1)  # 主会话（fork 用量已并入）；guardian 不计
        self.assertAlmostEqual(row["cost"], 2.5)
        self.assertEqual(row["tokens"], 1200)
        self.assertEqual(row["last_active"], "2026-09-26")
        self.assertEqual(row["status"]["needs_you"], ["真机试听"])
        self.assertEqual(len(rows), 1)  # 读不到 cwd 的会话不产生项目

    def test_session_cwd_info_reads_rollout_and_detects_subagent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            main, guardian = tmp / "a.jsonl", tmp / "b.jsonl"
            rollout(main, "/tmp/proj")
            rollout(guardian, "/tmp/proj", source={"subagent": {"other": "guardian"}})
            self.assertEqual(USAGE._codex_session_cwd_info(str(main)), ("/tmp/proj", False))
            self.assertEqual(USAGE._codex_session_cwd_info(str(guardian)), ("/tmp/proj", True))
            self.assertEqual(USAGE._codex_session_cwd_info(str(tmp / "none.jsonl")), ("", False))


if __name__ == "__main__":
    unittest.main()

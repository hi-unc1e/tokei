import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from test_codex_limits import USAGE


PRO_PLAN = {
    "planName": "Pro",
    "billingStrategy": "quota",
    "isDevinUser": True,
    "duration": 29,
    "startTimestamp": 1789296110000,
    "endTimestamp": 1791888110000,
    # -1 是付费套餐表示「不适用」的写法，不是计数
    "remainingMessages": -1,
    "totalMessages": -1,
    "dailyRemainingPercent": 98,
    "weeklyRemainingPercent": 99,
    "overageBalanceMicros": 10000000,
    "hideDailyQuota": False,
    "hideWeeklyQuota": False,
    "isDevinFree": False,
    "accountIdentityText": "someone@example.com - My Team",
}

FREE_PLAN = {
    "planName": "Free",
    "isDevinUser": True,
    "startTimestamp": 0,
    "endTimestamp": 0,
    "remainingMessages": 1750,
    "totalMessages": 2500,
    "dailyRemainingPercent": 100,
    "weeklyRemainingPercent": 100,
    "overageBalanceMicros": 0,
    "isDevinFree": True,
}


def write_store(support, rows, launched_at=None):
    """rows: {key: plan dict}；launched_at: Devin 启动时刻（logs/ 目录名）。"""
    storage = support / "User" / "globalStorage"
    storage.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(storage / "state.vscdb")
    connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    for key, plan in rows.items():
        connection.execute("INSERT INTO ItemTable VALUES (?, ?)", (key, json.dumps(plan)))
    connection.commit()
    connection.close()
    if launched_at is not None:
        (support / "logs" / launched_at.strftime("%Y%m%dT%H%M%S")).mkdir(parents=True)
    return support


def plan_key(account="user-" + "a" * 32):
    return f"windsurf.reactSettings.cachedPlanInfoData:{account}"


class DevinQuotaTests(unittest.TestCase):
    def setUp(self):
        self.old_dirs = USAGE.DEVIN_SUPPORT_DIRS
        self.old_env = dict(USAGE.os.environ)
        self.old_user_dir = USAGE._USER_DIR

    def tearDown(self):
        USAGE.DEVIN_SUPPORT_DIRS = self.old_dirs
        USAGE._USER_DIR = self.old_user_dir
        USAGE.os.environ.clear()
        USAGE.os.environ.update(self.old_env)

    def quota(self, rows, launched_at, now=None, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            support = write_store(Path(tmp) / "Devin", rows, launched_at)
            USAGE.DEVIN_SUPPORT_DIRS = [str(support)]
            USAGE._USER_DIR = tmp
            Path(tmp, "config.json").write_text("{}", encoding="utf-8")
            if now is None:
                return USAGE.fetch_devin_quota()
            plan, accounts = USAGE._devin_plan(
                support / "User" / "globalStorage" / "state.vscdb")
            return USAGE._normalize_devin_plan(
                plan, USAGE._devin_last_launch(str(support)), now=now,
                accounts=accounts, **kwargs)

    def windows(self, quota):
        return {window["id"]: window for window in quota["windows"]}

    def test_paid_plan_draws_both_percentages_and_the_overage_balance(self):
        now = datetime.now().astimezone()
        plan = dict(PRO_PLAN)
        plan["dailyResetAtUnix"] = int((now + timedelta(hours=3)).timestamp())
        plan["weeklyResetAtUnix"] = int((now + timedelta(days=4)).timestamp())
        quota = self.quota({plan_key(): plan}, now - timedelta(minutes=2))

        self.assertTrue(quota["available"])
        self.assertEqual(quota["plan"], "Devin Pro")
        self.assertEqual(quota["source"], "devin-app-cache")
        self.assertFalse(quota["stale"])

        windows = self.windows(quota)
        # 保存的是「剩余」，卡片画的是「已用」，只反转一次
        self.assertAlmostEqual(windows["devin-daily"]["used_pct"], 2)
        self.assertAlmostEqual(windows["devin-weekly"]["used_pct"], 1)
        self.assertEqual(windows["devin-daily"]["window_minutes"], 1440)
        self.assertEqual(windows["devin-weekly"]["window_minutes"], 10080)
        self.assertEqual(windows["devin-daily"]["reset"], plan["dailyResetAtUnix"])
        # -1 不是计数，不画消息额度
        self.assertNotIn("devin-messages", windows)
        self.assertEqual(quota["details"],
                         [{"label": "超额余额", "value": "$10.00"}])
        # 这一行自己带了人类可读账号，比键里那串 user-<32 hex> 有用
        self.assertEqual(quota["account"], "someone@example.com - My Team")

    def test_a_row_without_an_identity_shows_no_account(self):
        now = datetime.now().astimezone()
        plan = dict(PRO_PLAN, dailyResetAtUnix=int((now + timedelta(hours=2)).timestamp()))
        plan.pop("accountIdentityText")
        # 不拿键里那串 id 顶上
        self.assertIsNone(self.quota({plan_key(): plan}, now - timedelta(minutes=1))["account"])

    def test_free_plan_draws_its_message_pool(self):
        now = datetime.now().astimezone()
        quota = self.quota({plan_key(): FREE_PLAN}, now - timedelta(minutes=1))
        window = self.windows(quota)["devin-messages"]

        self.assertAlmostEqual(window["used_pct"], 30)
        # 消息池没有周期，也不编一个出来
        self.assertIsNone(window["window_minutes"])
        self.assertIsNone(window["reset"])
        self.assertEqual(window["detail"], "剩余 1,750 / 2,500 条")

    def test_hidden_window_is_dropped_and_booleans_are_not_percentages(self):
        now = datetime.now().astimezone()
        plan = dict(PRO_PLAN, hideDailyQuota=True, weeklyRemainingPercent=42)
        plan["weeklyResetAtUnix"] = int((now + timedelta(days=2)).timestamp())
        windows = self.windows(self.quota({plan_key(): plan}, now - timedelta(minutes=1)))

        self.assertNotIn("devin-daily", windows)
        self.assertAlmostEqual(windows["devin-weekly"]["used_pct"], 58)

    def test_a_window_whose_reset_has_passed_is_dropped_not_aged(self):
        now = datetime.now().astimezone()
        plan = dict(PRO_PLAN)
        plan["dailyResetAtUnix"] = int((now - timedelta(hours=1)).timestamp())
        plan["weeklyResetAtUnix"] = int((now + timedelta(days=3)).timestamp())
        windows = self.windows(self.quota({plan_key(): plan}, now - timedelta(minutes=30)))

        # 那个数字属于一个已经不存在的窗口
        self.assertNotIn("devin-daily", windows)
        self.assertIn("devin-weekly", windows)

    def test_a_snapshot_past_ten_minutes_is_stale_and_stamped_with_the_launch(self):
        now = datetime.now().astimezone()
        launched = now - timedelta(hours=5)
        plan = dict(PRO_PLAN)
        plan["dailyResetAtUnix"] = int((now + timedelta(hours=2)).timestamp())
        plan["weeklyResetAtUnix"] = int((now + timedelta(days=2)).timestamp())
        quota = self.quota({plan_key(): plan}, launched)

        self.assertTrue(quota["stale"])
        # 落款是那次启动，不是抓取时刻
        self.assertEqual(quota["updated"], int(launched.timestamp()))

    def test_an_undated_or_day_old_snapshot_is_not_shown_at_all(self):
        now = datetime.now().astimezone()
        plan = dict(PRO_PLAN, dailyResetAtUnix=int((now + timedelta(days=9)).timestamp()))

        self.assertEqual(self.quota({plan_key(): plan}, None), {})
        self.assertEqual(self.quota({plan_key(): plan}, now - timedelta(hours=25)), {})
        # 未来的落款同样不可信
        self.assertEqual(self.quota({plan_key(): plan}, now + timedelta(hours=1)), {})

    def test_an_unparseable_log_directory_carries_no_launch_evidence(self):
        self.assertIsNone(USAGE._devin_launch_stamp("not-a-stamp"))
        self.assertIsNone(USAGE._devin_launch_stamp("20261332T000000"))
        self.assertIsNone(USAGE._devin_launch_stamp("20260914T09200"))
        self.assertEqual(
            USAGE._devin_launch_stamp("20260914T092003"),
            datetime(2026, 9, 14, 9, 20, 3).astimezone())

    def test_two_accounts_resolve_to_the_longest_running_subscription(self):
        now = datetime.now().astimezone()
        reset = int((now + timedelta(hours=6)).timestamp())
        lapsed = dict(PRO_PLAN, planName="Lapsed", endTimestamp=1000,
                      dailyRemainingPercent=10, dailyResetAtUnix=reset)
        active = dict(PRO_PLAN, planName="Team", endTimestamp=9_999_999_999_999,
                      dailyRemainingPercent=80, dailyResetAtUnix=reset)
        quota = self.quota(
            {plan_key("user-" + "a" * 32): lapsed, plan_key("user-" + "b" * 32): active},
            now - timedelta(minutes=1))

        self.assertEqual(quota["plan"], "Devin Team")
        self.assertAlmostEqual(self.windows(quota)["devin-daily"]["used_pct"], 20)
        self.assertIn({"label": "本机账号", "value": "2 个",
                       "secondary": "取订阅期最长的一个"}, quota["details"])

    def test_the_renamed_windsurf_directory_is_read_too(self):
        now = datetime.now().astimezone()
        plan = dict(PRO_PLAN, dailyResetAtUnix=int((now + timedelta(hours=4)).timestamp()))
        with tempfile.TemporaryDirectory() as tmp:
            support = write_store(Path(tmp) / "Windsurf", {plan_key(): plan},
                                  now - timedelta(minutes=1))
            USAGE.DEVIN_SUPPORT_DIRS = [str(Path(tmp) / "Devin"), str(support)]
            self.assertTrue(USAGE.fetch_devin_quota()["available"])

    def test_a_machine_that_never_ran_devin_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            USAGE.DEVIN_SUPPORT_DIRS = [str(Path(tmp) / "Devin")]
            self.assertEqual(USAGE.fetch_devin_quota(), {})

    def test_the_quota_is_on_by_default_and_the_kill_switch_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            USAGE._USER_DIR = tmp
            Path(tmp, "config.json").write_text("{}", encoding="utf-8")
            USAGE._tokei_config.cache_clear() if hasattr(
                USAGE._tokei_config, "cache_clear") else None
            self.assertTrue(USAGE._provider_quota_enabled("devin"))
            USAGE.os.environ["TOKEI_DEVIN_QUOTA"] = "0"
            self.assertFalse(USAGE._provider_quota_enabled("devin"))
            with mock.patch.object(USAGE, "fetch_devin_quota",
                                   side_effect=AssertionError("read while disabled")):
                self.assertEqual(USAGE.scan_devin_quota(), {})


class DevinCLIUsageTests(unittest.TestCase):
    def setUp(self):
        self.old_paths = USAGE.DEVIN_CLI_DB_PATHS
        self.old_ledger = USAGE._LEDGER_FILE

    def tearDown(self):
        USAGE.DEVIN_CLI_DB_PATHS = self.old_paths
        USAGE._LEDGER_FILE = self.old_ledger
        USAGE._LEDGER_CACHE.update({"data": None, "dirty": False})

    def create_db(self, path, rows, sessions=()):
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, "
                           "model TEXT, working_directory TEXT, title TEXT)")
        connection.execute("CREATE TABLE message_nodes ("
                           "row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "session_id TEXT NOT NULL, node_id INTEGER NOT NULL, "
                           "parent_node_id INTEGER, chat_message TEXT NOT NULL, "
                           "created_at INTEGER NOT NULL)")
        for session_id, model in sessions:
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                               (session_id, model, "/tmp", "t"))
        for node_id, (session, message, created) in enumerate(rows, start=1):
            connection.execute(
                "INSERT INTO message_nodes "
                "(session_id, node_id, parent_node_id, chat_message, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session, node_id, None, json.dumps(message), created))
        connection.commit()
        connection.close()

    @staticmethod
    def assistant(model, metrics, created=None):
        metadata = {"generation_model": model, "metrics": metrics}
        if created is not None:
            metadata["created_at"] = created
        return {"role": "assistant", "metadata": metadata}

    def test_sibling_nodes_mirroring_one_turn_are_counted_once(self):
        """message_nodes 是森林：同一次回复会被写进两个兄弟节点，metrics 与
        metadata.created_at 完全一致。实测如此，照单全收会把用量翻一倍。"""
        now = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
        seconds = int(now.timestamp())
        stamp = "2026-09-20T13:49:51.721142Z"
        turn = self.assistant("claude-sonnet-4-5", {
            "input_tokens": 17349, "output_tokens": 74,
            "cache_read_tokens": 12160, "cache_creation_tokens": None,
        }, created=stamp)
        # 同一时刻但用量不同 = 两次真实调用，都要计
        other = self.assistant("claude-sonnet-4-5", {
            "input_tokens": 21, "output_tokens": 92,
            "cache_read_tokens": 29504, "cache_creation_tokens": None,
        }, created=stamp)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"
            self.create_db(db, [("s1", turn, seconds), ("s1", turn, seconds),
                                ("s1", other, seconds)])
            days = USAGE._scan_devin_cli_database(str(db))
        day = days[datetime.fromtimestamp(
            USAGE._provider_epoch(stamp)).astimezone().date().isoformat()]
        self.assertEqual(day["in"], 17370)
        self.assertEqual(day["out"], 166)
        self.assertEqual(day["cr"], 41664)

    def test_the_session_table_supplies_the_model_when_the_turn_names_none(self):
        now = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
        seconds = int(now.timestamp())
        turn = {"role": "assistant", "metadata": {"metrics": {"input_tokens": 100}}}
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"
            self.create_db(db, [("s1", turn, seconds)],
                           sessions=[("s1", "swe-1-6-slow")])
            days = USAGE._scan_devin_cli_database(str(db))
        self.assertIn("Swe 1 6 Slow", [USAGE.nice_model(m)
                                       for m in days[now.date().isoformat()]["models"]])

    def test_assistant_metrics_split_into_fresh_input_cache_and_output(self):
        now = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
        seconds = int(now.timestamp())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "sessions.db"
            self.create_db(db, [
                ("s1", self.assistant("claude-sonnet-4-5", {
                    "input_tokens": 11486, "output_tokens": 254,
                    "cache_read_tokens": 6450, "cache_creation_tokens": None,
                }), seconds),
                # 毫秒时间戳同样认；元数据自带的 created_at 优先
                ("s2", self.assistant("claude-sonnet-4-5", {
                    "input_tokens": 10, "output_tokens": 2,
                    "cache_read_tokens": 0, "cache_creation_tokens": 5,
                }, created=seconds * 1000), 0),
                # 没有 metrics 的消息不是用量
                ("s3", {"role": "user", "content": "hi"}, seconds),
                # 全零不计
                ("s4", self.assistant("claude-sonnet-4-5", {
                    "input_tokens": 0, "output_tokens": 0}), seconds),
                # 没有时间戳的消息跳过，不拿文件修改时间顶替
                ("s5", self.assistant("claude-sonnet-4-5",
                                      {"input_tokens": 99}), 0),
            ])
            USAGE.DEVIN_CLI_DB_PATHS = [str(db)]
            USAGE._LEDGER_FILE = str(root / "ledger.json")
            USAGE._LEDGER_CACHE.update({"data": None, "dirty": False})

            cache = {"v": USAGE._SCAN_CACHE_VERSION}
            result = USAGE.scan_devin(USAGE.range_bounds(), cache)
            # 签名没变就不重扫
            with mock.patch.object(USAGE, "_scan_devin_cli_database",
                                   side_effect=AssertionError("rescanned")):
                USAGE.scan_devin(USAGE.range_bounds(), cache)

        today = result["ranges"]["today"]
        self.assertEqual(today["in"], 11496)
        self.assertEqual(today["out"], 256)
        self.assertEqual(today["cr"], 6450)
        self.assertEqual(today["cw"], 5)
        self.assertEqual(today["reason"], 0)
        self.assertEqual(today["sessions"], {"s1", "s2"})

        price = USAGE._raw_price(USAGE._pricing_id("claude-sonnet-4-5"))
        expected = (11496 * price["in"] + 256 * price["out"]
                    + 6450 * price["cache_read"] + 5 * price["cache_write"]) / 1_000_000
        self.assertAlmostEqual(today["cost"], expected, places=6)

    def test_a_store_without_the_message_table_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE sessions (id TEXT)")
            connection.commit()
            connection.close()
            self.assertEqual(USAGE._scan_devin_cli_database(str(db)), {})

    def test_no_cli_database_reports_empty_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            USAGE.DEVIN_CLI_DB_PATHS = [str(Path(tmp) / "sessions.db")]
            USAGE._LEDGER_FILE = str(Path(tmp) / "ledger.json")
            USAGE._LEDGER_CACHE.update({"data": None, "dirty": False})
            result = USAGE.scan_devin(USAGE.range_bounds(), {"v": USAGE._SCAN_CACHE_VERSION})
        self.assertEqual(USAGE.token_total(result["ranges"]["all"]), 0)


if __name__ == "__main__":
    unittest.main()

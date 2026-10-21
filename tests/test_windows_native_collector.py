"""The collector only invokes Windows integrations through their opt-in gates."""
import importlib.util
import copy
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_collector_integration", ROOT / "usage.30s.py")
U = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = U
SPEC.loader.exec_module(U)


class WindowsCollectorTests(unittest.TestCase):
    def test_cli_disabled_never_loads_the_native_bridge(self):
        desktop = {"q5": 20}
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_provider_quota_enabled", return_value=False), \
                mock.patch.object(U, "_claude_quota_from_environment", return_value=None), \
                mock.patch.object(U, "_scan_claude_plan_raw", return_value=desktop), \
                mock.patch.object(U, "_windows_native_bridge") as bridge:
            self.assertEqual(U.scan_claude_plan(), desktop)
        bridge.assert_not_called()

    def test_cli_enabled_passes_desktop_data_to_the_bridge(self):
        desktop = {"q5": 20}
        bridge = mock.Mock()
        bridge.fetch_claude_quota.return_value = {"q5": 30, "q7": 40}
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_provider_quota_enabled", return_value=True), \
                mock.patch.object(U, "_claude_quota_from_environment", return_value=None), \
                mock.patch.object(U, "_scan_claude_plan_raw", return_value=desktop), \
                mock.patch.object(U, "_windows_native_bridge", return_value=bridge):
            self.assertEqual(U.scan_claude_plan()["q7"], 40)
        bridge.fetch_claude_quota.assert_called_once_with(desktop)

    def test_grok_ungranted_never_reads_a_cursor_login(self):
        bridge = mock.Mock()
        bridge.grok_status.return_value = {"status": "authorization_required"}
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_windows_native_bridge", return_value=bridge), \
                mock.patch.object(U, "_cursor_session") as cursor:
            self.assertIsNone(U.fetch_grok_bot_quota())
        cursor.assert_not_called()

    def test_grok_account_history_is_isolated_and_requires_current_authorization(self):
        bridge = mock.Mock(BridgeError=RuntimeError)
        bridge.grok_metadata.return_value = ({}, {}, "account-a", "profile")
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        def days(day, tokens):
            return {day: {"tokens": tokens, "out": tokens,
                          "models": {"fixture-model": {"tokens": tokens, "out": tokens}}}}

        cache = {U._GROK_BOT_PROVIDER_DAYS_CACHE_KEY: days(today, 9999)}
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_provider_quota_enabled", return_value=True), \
                mock.patch.object(U, "_windows_native_bridge", return_value=bridge), \
                mock.patch.object(U, "_load_ledger", return_value={"tools": {}}):
            key_a = U._grok_bot_provider_days_cache_key()
            first = {"grok_bot": {"usage": {"days": days(yesterday, 4321)}}}
            U._merge_grok_bot_provider_days(cache, first, key_a)
            second = {"grok_bot": {"usage": {"days": days(today, 1234)}}}
            U._merge_grok_bot_provider_days(cache, second, key_a)
            self.assertEqual(second["grok_bot"]["usage"]["ranges"]["all"]["tokens"], 5555)
            self.assertEqual(cache[U._GROK_BOT_PROVIDER_DAYS_CACHE_KEY][today]["tokens"], 9999)

            # Changing accounts revokes display of A's history, even while its cache remains on disk.
            bridge.grok_metadata.return_value = ({}, {}, "account-b", "profile")
            bridge.matching_grant.side_effect = RuntimeError("authorization_required")
            self.assertIsNone(U._grok_bot_provider_days_cache_key())
            denied = {"grok_bot": {}}
            U._merge_grok_bot_provider_days(cache, denied, None)
            self.assertEqual(denied["grok_bot"], {})
            dashboard = U.build_daily_costs("all", refresh=False, _cache=cache)
            self.assertFalse(any(row["tool"] == "grok_bot" for row in dashboard["models"]))

            bridge.matching_grant.side_effect = None
            key_b = U._grok_bot_provider_days_cache_key()
            self.assertNotEqual(key_a, key_b)
            fresh_b = {"grok_bot": {"usage": {"days": days(today, 10)}}}
            U._merge_grok_bot_provider_days(cache, fresh_b, key_b)
            self.assertEqual(fresh_b["grok_bot"]["usage"]["ranges"]["all"]["tokens"], 10)
            dashboard = U.build_daily_costs("all", refresh=False, _cache=cache)
            self.assertEqual(sum(row["tokens"] for row in dashboard["models"]
                                 if row["tool"] == "grok_bot"), 10)

            bridge.grok_metadata.return_value = ({}, {}, "account-a", "profile")
            restored_a = {"grok_bot": {}}
            U._merge_grok_bot_provider_days(cache, restored_a, U._grok_bot_provider_days_cache_key())
            self.assertEqual(restored_a["grok_bot"]["usage"]["ranges"]["all"]["tokens"], 5555)

    def test_grok_account_change_during_refresh_discards_the_in_flight_result(self):
        bridge = mock.Mock(BridgeError=RuntimeError)
        bridge.grok_metadata.return_value = ({}, {}, "account-a", "profile")
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_provider_quota_enabled", return_value=True), \
                mock.patch.object(U, "_windows_native_bridge", return_value=bridge):
            key_a = U._grok_bot_provider_days_cache_key()
            cache = {key_a: {date.today().isoformat(): {"tokens": 10}}}
            original = copy.deepcopy(cache)
            bridge.grok_metadata.return_value = ({}, {}, "account-b", "profile")
            quotas = {"grok_bot": {"usage": {"days": {date.today().isoformat(): {"tokens": 999}}}}}
            U._merge_grok_bot_provider_days(cache, quotas, key_a)
            self.assertEqual(quotas["grok_bot"], {})
            self.assertEqual(cache, original)

    def test_disabled_grok_history_never_loads_credentials_or_authorization(self):
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_provider_quota_enabled", return_value=False), \
                mock.patch.object(U, "_windows_native_bridge", side_effect=AssertionError("bridge forbidden")):
            self.assertIsNone(U._grok_bot_provider_days_cache_key())

    def test_grok_event_archive_keeps_the_captured_account_when_login_changes(self):
        bridge = mock.Mock(BridgeError=RuntimeError)
        identity_a, identity_b = ("account-a", "profile"), ("account-b", "profile")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        def payload(identity, tokens):
            return {"_tokei_account": identity[0], "_tokei_profile": identity[1],
                    "usageFetched": True, "usageStartDate": str(int(today.timestamp() * 1000)),
                    "usageEventsDisplay": [{"timestamp": str(int(today.timestamp() * 1000)),
                        "model": "fixture-model", "tokenUsage": {"inputTokens": tokens}}]}

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")), \
                mock.patch.object(U, "_provider_quota_enabled", return_value=True), \
                mock.patch.object(U, "_windows_native_bridge", return_value=bridge):
            bridge.grok_metadata.return_value = ({}, {}, *identity_b)
            self.assertIsNone(U._grok_bot_merge_events(payload(identity_a, 430), expected_identity=identity_a))
            self.assertFalse((Path(tmp) / "grok_bot_events").exists())

            bridge.grok_metadata.return_value = ({}, {}, *identity_a)
            directory_a = U._grok_bot_event_dir(identity_a)
            directory_b = U._grok_bot_event_dir(identity_b)
            write_json = U._atomic_write_json

            def switch_during_write(path, data):
                bridge.grok_metadata.return_value = ({}, {}, *identity_b)
                write_json(path, data)

            with mock.patch.object(U, "_atomic_write_json", side_effect=switch_during_write):
                U._grok_bot_merge_events(payload(identity_a, 430), expected_identity=identity_a)
            self.assertTrue(list(Path(directory_a).glob("*.json")))
            self.assertFalse(Path(directory_b).exists())

            merged_b = U._grok_bot_merge_events(payload(identity_b, 10), expected_identity=identity_b)
            usage_b = U._grok_bot_usage_from_bridge(merged_b)
            self.assertEqual(usage_b["ranges"]["all"]["tokens"], 10)
            self.assertEqual(len(merged_b["usageEventsDisplay"]), 1)

    def test_background_worker_is_hidden_and_uses_bundled_python(self):
        bridge = mock.Mock(__file__="fixture/native_bridge.py")
        with mock.patch.object(U, "_IS_WINDOWS", True), \
                mock.patch.object(U, "_windows_native_bridge", return_value=bridge), \
                mock.patch.object(U, "_grok_bot_helper_files", return_value=("fixture/result.json", "fixture/state.json")), \
                mock.patch.object(U, "_load_json", return_value={}), \
                mock.patch.object(U, "_grok_bot_events_since", return_value=1000), \
                mock.patch.object(U.os, "makedirs"), \
                mock.patch.object(U, "_atomic_write_json"), \
                mock.patch.object(U.subprocess, "Popen") as spawn:
            spawn.return_value.pid = 42
            self.assertEqual(U._grok_bot_helper_start("fixture-marker"), (True, True))
        args = spawn.call_args.args[0]
        self.assertEqual(args[:3], [sys.executable, bridge.__file__, "grok-data"])
        self.assertEqual(spawn.call_args.kwargs["creationflags"], 0x08000000)
        self.assertNotIn("/bin/sh", args)


if __name__ == "__main__":
    unittest.main()

"""Permanent regressions for compressed Codex rollout readers and caches.

All input is synthetic. CLI tests use an already installed zstd and skip when
absent. The raw-reader case is a behavioral double, not an installed-library test.
"""
import builtins
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    from .test_codex_limits import USAGE
    from .test_codex_zstd_rollout import _record, _token_count, _zstd_compress
except ImportError:
    from test_codex_limits import USAGE
    from test_codex_zstd_rollout import _record, _token_count, _zstd_compress


def _existing_cli():
    for executable in (shutil.which("zstd"), "/opt/homebrew/bin/zstd",
                       "/usr/local/bin/zstd", "/usr/bin/zstd"):
        if executable and os.path.isfile(executable) and os.access(executable, os.X_OK):
            return os.path.realpath(executable)
    return None


class CodexZstdRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tokei-codex-zstd-")
        self.addCleanup(temporary.cleanup)
        # The scanner keys files by canonical paths (/var aliases on macOS;
        # short temporary-directory names on Windows).
        self.root = Path(temporary.name).resolve()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.rollout = self.sessions / "rollout-fixture.jsonl.zst"
        self.bounds = {
            key: datetime(2024, 1, 8, tzinfo=timezone.utc)
            for key in ("today", "yesterday", "week", "last_week",
                        "last_week_end", "month", "year")
        }
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("CODEX_DIR", str(self.sessions)),
            ("CODEX_ARCHIVED_DIR", str(self.root / "archive")),
            ("_SCAN_CACHE_DIR", str(self.root / "cache")),
            ("_SCAN_CACHE_FILE", str(self.root / "cache" / "scan_cache.json")),
            ("_LEDGER_FILE", str(self.root / "ledger.json")),
            ("_LEDGER_CACHE", {"data": None, "dirty": False, "urgent": False}),
        ):
            self.stack.enter_context(mock.patch.object(USAGE, name, value))
        self.stack.enter_context(mock.patch.object(
            USAGE, "_load_tokei_config", return_value={}))
        self.live_limits = self.stack.enter_context(mock.patch.object(
            USAGE, "fetch_codex_live_limits",
            side_effect=AssertionError("offline test called live quota")))

    def payload(self, total=300):
        return ("\n".join([
            _record("2024-01-08T00:00:00Z", type="session_meta",
                    payload={"id": "shared-session", "cwd": "/synthetic/project"}),
            _record("2024-01-08T00:00:01Z", type="turn_context",
                    payload={"model": "gpt-5.5"}),
            _token_count("2024-01-08T00:02:00Z",
                         (total, 0, 25, 5), (total, 0, 25, 5)),
        ]) + "\n").encode("utf-8")

    def compressed(self, payload):
        data = _zstd_compress(payload)
        if data is not None:
            return data
        executable = _existing_cli()
        if executable is None:
            self.skipTest("no existing zstd encoder")
        return subprocess.run(
            [executable, "-c"], input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True, timeout=5).stdout

    @contextlib.contextmanager
    def cli_only(self):
        original_import = builtins.__import__

        def missing_decoders(name, *args, **kwargs):
            if name in ("compression", "compression.zstd", "zstandard"):
                raise ImportError("synthetic missing Python decoder")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=missing_decoders):
            yield

    def scan(self, cache=None):
        if cache is None:
            cache = {"v": USAGE._SCAN_CACHE_VERSION}
        result = USAGE.scan_codex(self.bounds, cache, local_only=True)
        self.live_limits.assert_not_called()
        return result["ranges"]["all"], cache

    def require_cli(self):
        executable = _existing_cli()
        if executable is None:
            self.skipTest("existing zstd CLI unavailable")
        return executable

    def test_cold_warm_and_compressed_rewrite(self):
        self.rollout.write_bytes(self.compressed(self.payload()))
        first, cache = self.scan()
        self.assertEqual((first["in"], first["out"]), (300, 25))
        with mock.patch.object(
                USAGE, "_iter_codex_usage_records",
                wraps=USAGE._iter_codex_usage_records) as parse:
            warm, cache = self.scan(cache)
        parse.assert_not_called()
        self.assertEqual((warm["in"], warm["out"]), (300, 25))
        self.rollout.write_bytes(self.compressed(self.payload(500)))
        # Make invalidation deterministic even on low-resolution filesystems.
        stat = self.rollout.stat()
        os.utime(self.rollout, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        updated, cache = self.scan(cache)
        self.assertEqual((updated["in"], updated["out"]), (500, 25))
        self.assertEqual(updated["sessions"], {"shared-session"})

    def test_plain_and_compressed_mirrors_count_once(self):
        self.rollout.write_bytes(self.compressed(self.payload()))
        (self.sessions / "rollout-fixture.jsonl").write_bytes(self.payload())
        initial, cache = self.scan()
        warm, _ = self.scan(cache)
        for result in (initial, warm):
            self.assertEqual((result["in"], result["out"]), (300, 25))
            self.assertEqual(result["sessions"], {"shared-session"})
        canonical = [entry for entry in cache["codex"].values()
                     if entry.get("canonical")]
        self.assertEqual(len(canonical), 1)

    def test_corrupt_real_cli_does_not_cache_empty_session(self):
        self.require_cli()
        self.rollout.write_bytes(b"not a zstd frame")
        with self.cli_only():
            result, cache = self.scan()
        self.assertEqual(result["in"], 0)
        self.assertNotIn(str(self.rollout), cache["codex"])

    def test_real_cli_nonzero_status_is_raised_and_reaped(self):
        self.require_cli()
        self.rollout.write_bytes(b"not a zstd frame")
        spawn = subprocess.Popen
        children = []

        def track(*args, **kwargs):
            process = spawn(*args, **kwargs)
            children.append(process)
            return process

        with self.cli_only(), mock.patch.object(
                USAGE.subprocess, "Popen", side_effect=track):
            with self.assertRaises(OSError):
                with USAGE._open_codex_rollout(self.rollout) as stream:
                    while stream.read(64 * 1024):
                        pass
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertTrue(all(child.returncode != 0 for child in children))

    def test_existing_path_executable_is_used(self):
        executable = self.require_cli()
        directory = self.root / "bin with spaces"
        directory.mkdir()
        alias = directory / ("zstd.exe" if os.name == "nt" else "zstd")
        try:
            alias.symlink_to(executable)
        except OSError as exc:
            self.skipTest(f"cannot create CLI test symlink: {exc}")
        self.rollout.write_bytes(self.compressed(self.payload()))
        fixed_paths = {"/opt/homebrew/bin/zstd",
                       "/usr/local/bin/zstd", "/usr/bin/zstd"}
        exists = os.path.exists
        spawn = subprocess.Popen

        def without_fixed_paths(path):
            return False if os.fspath(path) in fixed_paths else exists(path)

        with self.cli_only(), \
             mock.patch.dict(os.environ, {"PATH": str(directory)}), \
             mock.patch.object(USAGE.os.path, "exists",
                               side_effect=without_fixed_paths), \
             mock.patch.object(USAGE.subprocess, "Popen",
                               wraps=spawn) as launch:
            with USAGE._open_codex_rollout(self.rollout) as stream:
                self.assertEqual(stream.read(), self.payload())
        command = launch.call_args.args[0]
        # shutil.which may return the PATHEXT spelling zstd.EXE on Windows.
        # Keep checking the PATH alias location using filesystem case rules.
        self.assertEqual(os.path.normcase(command[0]), os.path.normcase(str(alias)))
        self.assertEqual(command[1:], ["-dc", "--", str(self.rollout)])

    def test_raw_library_reader_is_buffered_for_metadata(self):
        payload = self.payload()
        # A real file handle is owned by this raw-reader behavioral double.
        class RawReader(io.RawIOBase):
            def __init__(self, source):
                self.source = source
                self.data = io.BytesIO(payload)

            def readable(self):
                return True

            def readinto(self, target):
                return self.data.readinto(target)

            def read(self, size=-1):
                return self.data.read(size)

            def readline(self, size=-1):
                raise io.UnsupportedOperation("raw reader has no readline")

            def close(self):
                self.source.close()
                self.data.close()
                super().close()

        fake = types.SimpleNamespace(ZstdDecompressor=lambda:
            types.SimpleNamespace(stream_reader=RawReader))
        original_import = builtins.__import__

        def library_only(name, *args, **kwargs):
            if name in ("compression", "compression.zstd"):
                raise ImportError("simulate pre-3.14")
            if name == "zstandard":
                return fake
            return original_import(name, *args, **kwargs)

        self.rollout.write_bytes(b"synthetic raw-reader input")
        with mock.patch("builtins.__import__", side_effect=library_only):
            self.assertEqual(USAGE._codex_session_meta(self.rollout),
                             ("shared-session", None, None))
            self.assertEqual(USAGE._codex_session_cwd(self.rollout),
                             "/synthetic/project")
            with USAGE._open_codex_rollout(self.rollout) as stream:
                self.assertEqual(stream.readline(4), payload[:4])

    def test_parser_upgrade_rebuilds_old_decoder_dependent_cache(self):
        self.rollout.write_bytes(self.compressed(self.payload()))
        _, cache = self.scan()
        entry = cache["codex"][str(self.rollout)]
        entry["parser_version"] = 8
        with mock.patch.object(
                USAGE, "_iter_codex_usage_records",
                wraps=USAGE._iter_codex_usage_records) as parse:
            recovered, _ = self.scan(cache)
        self.assertTrue(parse.called)
        self.assertEqual(cache["codex"][str(self.rollout)]["parser_version"],
                         USAGE._CODEX_PARSER_VERSION)
        self.assertEqual(recovered["in"], 300)



    def test_actual_optional_raw_reader_handles_metadata_and_usage(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("optional zstandard unavailable")
        self.rollout.write_bytes(self.compressed(self.payload()))
        original_import = builtins.__import__
        constructor = zstandard.ZstdDecompressor

        def optional_only(name, *args, **kwargs):
            if name in ("compression", "compression.zstd"):
                raise ImportError("exercise installed zstandard")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=optional_only), \
             mock.patch.object(zstandard, "ZstdDecompressor",
                               wraps=constructor) as decoder:
            self.assertEqual(USAGE._codex_session_meta(self.rollout),
                             ("shared-session", None, None))
            self.assertEqual(USAGE._codex_session_cwd(self.rollout),
                             "/synthetic/project")
            with USAGE._open_codex_rollout(self.rollout) as stream:
                self.assertEqual(stream.readline(7), self.payload()[:7])
            result, _ = self.scan()
        self.assertGreaterEqual(decoder.call_count, 3)
        self.assertEqual((result["in"], result["out"]), (300, 25))

    def test_large_rollout_history_is_not_subject_to_event_size_cap(self):
        # OpenClaw's 4-MiB event limit must not become a Codex history limit.
        large_line = _record(
            "2024-01-08T00:03:00Z", type="response_item",
            payload={"type": "message", "text": "x" * (9 * 1024 * 1024)})
        tail = _token_count("2024-01-08T00:04:00Z",
                            (500, 0, 25, 5), (200, 0, 0, 0))
        payload = self.payload() + (large_line + "\n" + tail + "\n").encode()
        self.rollout.write_bytes(self.compressed(payload))
        result, cache = self.scan()
        self.assertEqual(result["in"], 500)
        warm, _ = self.scan(cache)
        self.assertEqual(warm["in"], 500)
        try:
            import zstandard
        except ImportError:
            return
        original_import = builtins.__import__

        def optional_only(name, *args, **kwargs):
            if name in ("compression", "compression.zstd"):
                raise ImportError("exercise large history through installed module")
            return original_import(name, *args, **kwargs)

        constructor = zstandard.ZstdDecompressor
        with mock.patch("builtins.__import__", side_effect=optional_only), \
             mock.patch.object(zstandard, "ZstdDecompressor",
                               wraps=constructor) as decoder:
            actual_module, _ = self.scan()
        self.assertTrue(decoder.called)
        self.assertEqual(actual_module["in"], 500)

if __name__ == "__main__":
    unittest.main()

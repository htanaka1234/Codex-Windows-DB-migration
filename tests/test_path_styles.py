import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from codex_path_styles import (  # noqa: E402
    PathConversionError,
    is_windows_absolute_path,
    strip_long_windows_prefix,
    to_windows_path,
    to_wsl_path,
    windows_path_problem,
)
from codex_agent_config import (  # noqa: E402
    audit_agent_config,
    normalize_agent_config_value,
    rewrite_agent_config,
)


class PathStylesTest(unittest.TestCase):
    def test_extended_unc_prefix_is_preserved_as_standard_unc(self):
        value = r"\\?\UNC\wsl.localhost\Ubuntu\home\user\project"
        self.assertEqual(
            strip_long_windows_prefix(value),
            r"\\wsl.localhost\Ubuntu\home\user\project",
        )

    def test_device_unc_prefix_is_preserved_as_standard_unc(self):
        value = r"\\.\UNC\server\share\project"
        self.assertEqual(strip_long_windows_prefix(value), r"\\server\share\project")

    def test_windows_conversion_repairs_malformed_unc_remnant(self):
        value = r"UNC\wsl.localhost\Ubuntu\home\user\project"
        self.assertEqual(
            to_windows_path(value, long_prefix=False),
            r"\\wsl.localhost\Ubuntu\home\user\project",
        )

    def test_mnt_drive_converts_to_drive_absolute_path(self):
        self.assertEqual(to_windows_path("/mnt/c/src/project", long_prefix=False), r"C:\src\project")
        self.assertEqual(to_windows_path("/mnt/c/src/project", long_prefix=True), r"\\?\C:\src\project")

    def test_wsl_native_path_requires_distro(self):
        with self.assertRaisesRegex(PathConversionError, "requires --wsl-distro"):
            to_windows_path("/home/user/project", long_prefix=False)

    def test_wsl_native_path_converts_to_wsl_unc_with_distro(self):
        self.assertEqual(
            to_windows_path("/home/user/project", long_prefix=False, wsl_distro="Ubuntu"),
            r"\\wsl.localhost\Ubuntu\home\user\project",
        )
        self.assertEqual(
            to_windows_path("/home/user/project", long_prefix=True, wsl_distro="Ubuntu"),
            r"\\?\UNC\wsl.localhost\Ubuntu\home\user\project",
        )

    def test_single_backslash_and_relative_paths_are_rejected(self):
        for value in (r"\home\user\project", r"relative\project"):
            with self.subTest(value=value), self.assertRaises(PathConversionError):
                to_windows_path(value, long_prefix=False, wsl_distro="Ubuntu")

    def test_windows_absolute_validation(self):
        valid = [
            r"C:\Users\user\project",
            r"\\wsl.localhost\Ubuntu\home\user\project",
            r"\\?\C:\Users\user\project",
            r"\\?\UNC\wsl.localhost\Ubuntu\home\user\project",
        ]
        for value in valid:
            with self.subTest(value=value):
                self.assertTrue(is_windows_absolute_path(value))
                self.assertIsNone(windows_path_problem(value))
        for value in (r"UNC\server\share", r"\home\user", "/home/user", "/mnt/c/src", r"C:\mnt\c\src"):
            with self.subTest(value=value):
                self.assertIsNotNone(windows_path_problem(value))

    def test_wsl_unc_round_trip(self):
        value = r"\\?\UNC\wsl.localhost\Ubuntu\home\user\project"
        self.assertEqual(to_wsl_path(value), "/home/user/project")


class ScriptSafetyTest(unittest.TestCase):
    def _make_codex_home(self, root: Path, cwd: str) -> Path:
        home = root / ".codex"
        session_dir = home / "sessions" / "2026" / "08" / "01"
        session_dir.mkdir(parents=True)
        rollout = session_dir / "rollout-test.jsonl"
        rollout.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "thread-1", "cwd": cwd, "source": "vscode"}})
            + "\n",
            encoding="utf-8",
        )
        db = sqlite3.connect(home / "state_5.sqlite")
        db.execute(
            "create table threads (id text, cwd text, source text, thread_source text, archived integer, "
            "title text, updated_at_ms integer, preview text, first_user_message text, rollout_path text)"
        )
        db.execute(
            "insert into threads values (?,?,?,?,?,?,?,?,?,?)",
            ("thread-1", cwd, "vscode", "user", 0, "test", 1, "preview", "message", str(rollout)),
        )
        db.commit()
        db.close()
        (home / "session_index.jsonl").write_text(
            json.dumps({"id": "thread-1", "thread_name": "test", "updated_at": "2026-08-01T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        (home / ".codex-global-state.json").write_text(
            json.dumps({
                "active-workspace-roots": [cwd],
                "electron-saved-workspace-roots": [cwd],
                "project-order": [cwd],
                "thread-workspace-root-hints": {"thread-1": cwd},
            }),
            encoding="utf-8",
        )
        return home

    def _run(self, script: str, *args: str):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            text=True,
            capture_output=True,
            check=False,
            env={key: value for key, value in os.environ.items() if key != "WSL_DISTRO_NAME"},
        )

    def test_rollout_dry_run_refuses_unmapped_wsl_native_path(self):
        with tempfile.TemporaryDirectory() as temp:
            home = self._make_codex_home(Path(temp), "/home/user/project")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "repair_rollout_session_meta.py"),
                    "dry-run",
                    "--target-style",
                    "windows",
                    "--codex-home",
                    str(home),
                ],
                text=True,
                capture_output=True,
                check=False,
                env={key: value for key, value in os.environ.items() if key != "WSL_DISTRO_NAME"},
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --wsl-distro", result.stdout)

    def test_rollout_dry_run_accepts_wsl_native_path_with_distro(self):
        with tempfile.TemporaryDirectory() as temp:
            home = self._make_codex_home(Path(temp), "/home/user/project")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "repair_rollout_session_meta.py"),
                    "dry-run",
                    "--target-style",
                    "windows",
                    "--wsl-distro",
                    "Ubuntu",
                    "--codex-home",
                    str(home),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(r"\\wsl.localhost\\Ubuntu\\home\\user\\project", result.stdout)

    def test_end_to_end_windows_conversion_leaves_only_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self._make_codex_home(root, "/home/user/project")
            common = ("--target-style", "windows", "--wsl-distro", "Ubuntu", "--codex-home", str(home))
            for script in ("repair_rollout_session_meta.py", "normalize_history_cwd.py", "repair_ui_indexes.py"):
                result = self._run(script, "apply", *common, "--backup-root", str(root))
                self.assertEqual(result.returncode, 0, f"{script}: {result.stdout}\n{result.stderr}")

            state = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
            expected_ui = r"\\wsl.localhost\Ubuntu\home\user\project"
            self.assertEqual(state["active-workspace-roots"], [expected_ui])
            self.assertEqual(state["thread-workspace-root-hints"]["thread-1"], expected_ui)
            db = sqlite3.connect(home / "state_5.sqlite")
            self.assertEqual(
                db.execute("select cwd from threads where id='thread-1'").fetchone()[0],
                r"\\?\UNC\wsl.localhost\Ubuntu\home\user\project",
            )
            db.close()

            audit = self._run("current_history_visibility_audit.py", *common)
            self.assertEqual(audit.returncode, 0, f"{audit.stdout}\n{audit.stderr}")
            self.assertIn('"problem_count": 0', audit.stdout)


class AgentConfigPathTest(unittest.TestCase):
    def test_relative_agent_path_remains_relative_by_default(self):
        config = Path("/mnt/c/Users/user/.codex/config.toml")
        value = "./agents/reviewer.toml"
        self.assertEqual(normalize_agent_config_value(value, config, "windows", None), value)
        self.assertEqual(
            normalize_agent_config_value(
                value,
                config,
                "windows",
                None,
                absolutize_relative=True,
            ),
            r"C:\Users\user\.codex\agents\reviewer.toml",
        )

    def test_audit_accepts_documented_relative_agent_path(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text(
                '[agents.reviewer]\ndescription = "Review code"\nconfig_file = "./agents/reviewer.toml"\n',
                encoding="utf-8",
            )
            audit = audit_agent_config(config, "wsl")
            self.assertEqual(audit["problems"], [])
            self.assertTrue(audit["entries"][0]["declared_relative"])

    def test_malformed_unc_agent_path_is_not_treated_as_relative(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text(
                "[agents.reviewer]\nconfig_file = 'UNC\\\\wsl.localhost\\\\Ubuntu\\\\home\\\\user\\\\reviewer.toml'\n",
                encoding="utf-8",
            )
            audit = audit_agent_config(config, "windows", "Ubuntu")
            self.assertFalse(audit["entries"][0]["declared_relative"])
            self.assertEqual(len(audit["problems"]), 1)
            self.assertIn("missing its leading double backslash", audit["problems"][0]["problem"])

    def test_rewrite_preserves_surrounding_config_and_comment(self):
        original = '[agents.reviewer]\nconfig_file = "/home/user/reviewer.toml" # role\nmodel = "gpt-5"\n'
        rewritten = rewrite_agent_config(
            original,
            ["/home/user/reviewer.toml"],
            [r"\\wsl.localhost\Ubuntu\home\user\reviewer.toml"],
        )
        self.assertIn('# role', rewritten)
        self.assertIn('model = "gpt-5"', rewritten)
        self.assertIn(r'config_file = "\\\\wsl.localhost\\Ubuntu\\home\\user\\reviewer.toml"', rewritten)

    def test_repair_script_converts_absolute_wsl_agent_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".codex"
            home.mkdir()
            config = home / "config.toml"
            config.write_text(
                '[agents.reviewer]\nconfig_file = "/home/user/agents/reviewer.toml"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "repair_agent_config_paths.py"),
                    "apply",
                    "--target-style",
                    "windows",
                    "--wsl-distro",
                    "Ubuntu",
                    "--codex-home",
                    str(home),
                    "--backup-root",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            import tomllib

            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(
                parsed["agents"]["reviewer"]["config_file"],
                r"\\wsl.localhost\Ubuntu\home\user\agents\reviewer.toml",
            )
            self.assertEqual(len(list(root.glob("config.before-agent-path-repair-windows-*.toml"))), 1)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from patent_summarizer import licensing


TEST_ANNUAL_CODE = "TEST-ANNUAL-CODE"
TEST_PERMANENT_CODE = "TEST-PERMANENT-CODE"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LicensingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)
        self.code_hashes = patch.multiple(
            licensing,
            ANNUAL_CODE_HASH=_digest(TEST_ANNUAL_CODE),
            PERMANENT_CODE_HASH=_digest(TEST_PERMANENT_CODE),
        )
        self.code_hashes.start()
        self.addCleanup(self.code_hashes.stop)

    def test_first_start_creates_365_day_license_and_allows_gui(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"

            allowed = licensing.startup_allows_gui([], state_path=state_path, now=self.now)

            self.assertTrue(allowed)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["mode"], "annual")
            self.assertEqual(
                datetime.fromisoformat(state["expires_at"]),
                self.now + timedelta(days=365),
            )

    def test_unexpired_license_allows_gui_but_exact_expiry_blocks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"
            licensing.startup_allows_gui([], state_path=state_path, now=self.now)

            self.assertTrue(
                licensing.startup_allows_gui(
                    [],
                    state_path=state_path,
                    now=self.now + timedelta(days=365) - timedelta(microseconds=1),
                )
            )
            self.assertFalse(
                licensing.startup_allows_gui(
                    [],
                    state_path=state_path,
                    now=self.now + timedelta(days=365),
                )
            )

    def test_annual_code_resets_expiry_from_repair_time_even_if_still_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"
            licensing.startup_allows_gui([], state_path=state_path, now=self.now)
            repair_time = self.now + timedelta(days=30)

            launch_gui = licensing.startup_allows_gui(
                ["--repair", TEST_ANNUAL_CODE],
                state_path=state_path,
                now=repair_time,
            )

            self.assertFalse(launch_gui)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                datetime.fromisoformat(state["expires_at"]),
                repair_time + timedelta(days=365),
            )

    def test_annual_code_repairs_expired_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"
            licensing.startup_allows_gui([], state_path=state_path, now=self.now)
            repair_time = self.now + timedelta(days=400)

            licensing.startup_allows_gui(
                ["--repair", TEST_ANNUAL_CODE],
                state_path=state_path,
                now=repair_time,
            )

            self.assertTrue(
                licensing.startup_allows_gui(
                    [],
                    state_path=state_path,
                    now=repair_time + timedelta(days=364),
                )
            )

    def test_annual_code_repairs_corrupt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"
            state_path.write_text("not-json", encoding="utf-8")

            licensing.startup_allows_gui(
                ["--repair", TEST_ANNUAL_CODE],
                state_path=state_path,
                now=self.now,
            )

            self.assertTrue(
                licensing.startup_allows_gui(
                    [],
                    state_path=state_path,
                    now=self.now + timedelta(days=364),
                )
            )

    def test_permanent_code_allows_forever_and_annual_code_does_not_downgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"
            licensing.startup_allows_gui(
                ["--repair", TEST_PERMANENT_CODE],
                state_path=state_path,
                now=self.now,
            )
            permanent_state = state_path.read_bytes()

            licensing.startup_allows_gui(
                ["--repair", TEST_ANNUAL_CODE],
                state_path=state_path,
                now=self.now + timedelta(days=1000),
            )

            self.assertEqual(state_path.read_bytes(), permanent_state)
            self.assertTrue(
                licensing.startup_allows_gui(
                    [],
                    state_path=state_path,
                    now=self.now + timedelta(days=10000),
                )
            )

    def test_wrong_or_malformed_repair_command_is_silent_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.dat"
            licensing.startup_allows_gui([], state_path=state_path, now=self.now)
            original = state_path.read_bytes()

            commands = [
                ["--repair"],
                ["--repair", "WRONG"],
                ["--repair", TEST_ANNUAL_CODE, "EXTRA"],
            ]
            for command in commands:
                with self.subTest(command=command):
                    self.assertFalse(
                        licensing.startup_allows_gui(
                            command,
                            state_path=state_path,
                            now=self.now + timedelta(days=400),
                        )
                    )
                    self.assertEqual(state_path.read_bytes(), original)

    def test_corrupt_state_or_unwritable_location_blocks_gui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corrupt_path = root / "corrupt.dat"
            corrupt_path.write_text("not-json", encoding="utf-8")
            self.assertFalse(
                licensing.startup_allows_gui([], state_path=corrupt_path, now=self.now)
            )

            parent_file = root / "not-a-directory"
            parent_file.write_text("occupied", encoding="utf-8")
            self.assertFalse(
                licensing.startup_allows_gui(
                    [],
                    state_path=parent_file / "runtime.dat",
                    now=self.now,
                )
            )

    def test_default_state_path_uses_local_app_data(self):
        path = licensing.default_state_path({"LOCALAPPDATA": r"C:\Users\Example\AppData\Local"})

        self.assertEqual(
            path,
            Path(r"C:\Users\Example\AppData\Local") / "PatentSummarizer" / "runtime.dat",
        )


if __name__ == "__main__":
    unittest.main()

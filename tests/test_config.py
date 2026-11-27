import os
import tempfile
import unittest
from pathlib import Path

from patent_summarizer.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_loads_required_openai_settings_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://example.test/v1",
                        "OPENAI_API_KEY=secret",
                        "OPENAI_MODEL=model-a",
                        "PATENT_API_RPM=10",
                        "PATENT_MAX_RETRIES=5",
                        "PATENT_API_TIMEOUT_SECONDS=275.5",
                        "PATENT_OCR_ENABLED=true",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(env_path=env_path, environ={})

        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.model, "model-a")
        self.assertEqual(config.api_rpm, 10)
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.api_timeout_seconds, 275.5)
        self.assertTrue(config.ocr_enabled)

    def test_environment_overrides_env_file_and_cli_overrides_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://file.test/v1",
                        "OPENAI_API_KEY=file-key",
                        "OPENAI_MODEL=file-model",
                        "PATENT_API_RPM=10",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(
                env_path=env_path,
                environ={
                    "OPENAI_BASE_URL": "https://env.test/v1",
                    "OPENAI_API_KEY": "env-key",
                    "OPENAI_MODEL": "env-model",
                    "PATENT_API_RPM": "8",
                },
                api_rpm=4,
            )

        self.assertEqual(config.base_url, "https://env.test/v1")
        self.assertEqual(config.api_key, "env-key")
        self.assertEqual(config.model, "env-model")
        self.assertEqual(config.api_rpm, 4)

    def test_missing_required_settings_raise_clear_error(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(env_path=Path("missing.env"), environ={})

        self.assertIn("OPENAI_BASE_URL", str(ctx.exception))
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))
        self.assertIn("OPENAI_MODEL", str(ctx.exception))

    def test_api_timeout_defaults_to_300_and_rejects_non_positive_values(self):
        required = {
            "OPENAI_BASE_URL": "https://example.test/v1",
            "OPENAI_API_KEY": "key",
            "OPENAI_MODEL": "model",
        }
        self.assertEqual(
            300.0,
            load_config(env_path=Path("missing.env"), environ=required).api_timeout_seconds,
        )
        with self.assertRaisesRegex(ConfigError, "PATENT_API_TIMEOUT_SECONDS"):
            load_config(
                env_path=Path("missing.env"),
                environ={**required, "PATENT_API_TIMEOUT_SECONDS": "0"},
            )


class GitHubActionsWorkflowTests(unittest.TestCase):
    def test_ci_workflow_tests_pushes_and_pull_requests_on_two_operating_systems(self):
        workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("master", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn('"3.10"', workflow)
        self.assertIn('"3.13"', workflow)
        self.assertIn('actions/checkout@v6', workflow)
        self.assertIn('actions/setup-python@v6', workflow)
        self.assertIn('python -m unittest discover', workflow)

    def test_release_workflow_builds_and_publishes_windows_portable_package(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn('"v*"', workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("python build_portable.py", workflow)
        self.assertIn("Compress-Archive", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("contents: write", workflow)

    def test_portable_build_keeps_original_env_example_filename(self):
        build_script = Path("build_portable.py").read_text(encoding="utf-8")

        self.assertIn("shutil.copy2(env_example, dist_app / env_example.name)", build_script)

    def test_ci_cd_guide_explains_the_first_experiment(self):
        guide = Path("CI-CD实验指南.md").read_text(encoding="utf-8")

        for phrase in ["什么是 CI/CD", "第一次上传", "查看 CI", "手动构建", "发布一个版本", "v0.1.0"]:
            self.assertIn(phrase, guide)


if __name__ == "__main__":
    unittest.main()

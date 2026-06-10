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


if __name__ == "__main__":
    unittest.main()

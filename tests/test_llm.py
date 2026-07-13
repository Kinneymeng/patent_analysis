import json
import tempfile
import unittest
from pathlib import Path

from patent_summarizer.llm import (
    HttpResponseError,
    RateLimiter,
    RateLimitedOpenAIClient,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class LlmTests(unittest.TestCase):
    def test_rate_limiter_enforces_minimum_interval(self):
        clock = FakeClock()
        limiter = RateLimiter(rpm=10, clock=clock.time, sleeper=clock.sleep, safety_seconds=0.5)

        limiter.wait()
        limiter.wait()
        limiter.wait()

        self.assertEqual(clock.sleeps, [6.5, 6.5])

    def test_retries_retryable_http_errors_then_returns_content(self):
        calls = []

        def transport(_url, _headers, _payload, _timeout):
            calls.append(1)
            if len(calls) < 3:
                raise HttpResponseError(500, "server error")
            return {"choices": [{"message": {"content": "ok"}}]}

        clock = FakeClock()
        client = RateLimitedOpenAIClient(
            base_url="https://example.test/v1",
            api_key="key",
            model="model",
            rpm=600,
            max_retries=5,
            transport=transport,
            clock=clock.time,
            sleeper=clock.sleep,
            jitter=lambda _attempt: 0,
        )

        result = client.chat("system", "user")

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(clock.sleeps, [1, 2])

    def test_does_not_retry_auth_errors(self):
        calls = []

        def transport(_url, _headers, _payload, _timeout):
            calls.append(1)
            raise HttpResponseError(401, "unauthorized")

        client = RateLimitedOpenAIClient(
            base_url="https://example.test/v1",
            api_key="key",
            model="model",
            rpm=600,
            max_retries=5,
            transport=transport,
            sleeper=lambda _seconds: None,
        )

        with self.assertRaises(HttpResponseError):
            client.chat("system", "user")

        self.assertEqual(len(calls), 1)

    def test_uses_retry_after_for_429(self):
        calls = []
        clock = FakeClock()

        def transport(_url, _headers, _payload, _timeout):
            calls.append(1)
            if len(calls) == 1:
                raise HttpResponseError(429, "rate limited", retry_after=12)
            return {"choices": [{"message": {"content": "ok"}}]}

        client = RateLimitedOpenAIClient(
            base_url="https://example.test/v1",
            api_key="key",
            model="model",
            rpm=600,
            max_retries=2,
            transport=transport,
            clock=clock.time,
            sleeper=clock.sleep,
        )

        self.assertEqual(client.chat("system", "user"), "ok")
        self.assertEqual(clock.sleeps, [12])

    def test_debug_log_records_payload_and_attempts_without_api_key(self):
        calls = []

        def transport(_url, _headers, _payload, _timeout):
            calls.append(1)
            if len(calls) == 1:
                raise HttpResponseError(500, "wrapped upstream error")
            return {
                "model": "model-a",
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            debug_path = Path(temp_dir) / "llm-debug.jsonl"
            client = RateLimitedOpenAIClient(
                base_url="https://example.test/v1",
                api_key="secret-key",
                model="model-a",
                rpm=600,
                max_retries=1,
                transport=transport,
                sleeper=lambda _seconds: None,
                jitter=lambda _attempt: 0,
                debug_path=debug_path,
            )

            self.assertEqual(client.chat("system text", "user text"), "ok")
            records = [json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([record["event"] for record in records], ["request", "attempt", "attempt"])
        self.assertEqual(records[0]["payload"]["model"], "model-a")
        self.assertEqual(records[0]["payload"]["messages"][1]["content"], "user text")
        self.assertNotIn("secret-key", json.dumps(records, ensure_ascii=False))
        self.assertEqual(records[1]["http_status"], 500)
        self.assertTrue(records[1]["will_retry"])
        self.assertEqual(records[2]["response_model"], "model-a")
        self.assertEqual(records[2]["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()

import unittest

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


if __name__ == "__main__":
    unittest.main()

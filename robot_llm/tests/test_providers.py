"""Pure test doubles. These tests never bind a socket or contact an endpoint."""
from dataclasses import replace
import json
import socket
import unittest
from unittest.mock import MagicMock, patch

from robot_llm.contract import intent_schema
from robot_llm.providers import (EndpointConfig, InferenceProvider, MAX_RESPONSE_BYTES,
    ProviderError, build_payload, extract_final_content, post_completion)

STOP = '{"schema_version":1,"kind":"stop_request"}'
CAPS = {"forward_m_s": [-.04, .04], "max_duration_ms": 1000}


def envelope(content=STOP, **updates):
    message = {"role": "assistant", "content": content}
    message.update(updates)
    return {"choices": [{"finish_reason": "stop", "message": message}]}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = EndpointConfig("http://127.0.0.1:8000/v1", "explicit-test-model")

    def test_network_opt_in_is_enforced_before_connection(self):
        with patch("robot_llm.providers.http.client.HTTPConnection") as connection:
            with self.assertRaisesRegex(ProviderError, "network disabled"):
                post_completion(self.config, {})
            connection.assert_not_called()

    def test_constructor_does_not_contact_endpoint(self):
        with patch("socket.create_connection", side_effect=AssertionError("no network")):
            InferenceProvider("native-schema", self.config)
            build_payload("native-schema", self.config, "Stop.", CAPS)

    def test_schema_and_shared_prompt_are_identical(self):
        plain = build_payload("json-object", self.config, "Stop.", CAPS)
        native = build_payload("native-schema", self.config, "Stop.", CAPS)
        self.assertEqual(plain["messages"], native["messages"])
        self.assertEqual(plain["response_format"], {"type": "json_object"})
        self.assertEqual(native["response_format"]["json_schema"]["schema"], intent_schema())
        self.assertTrue(native["response_format"]["json_schema"]["strict"])
        for body in (plain, native):
            self.assertIs(body["stream"], False)
            self.assertEqual(body["n"], 1)
            self.assertEqual(body["temperature"], 0)
            self.assertNotIn("tools", body)

    def test_vllm_structured_outputs_explicit_wire_format(self):
        config = replace(self.config, schema_transport="structured-outputs")
        payload = build_payload("native-schema", config, "Stop.", CAPS)
        self.assertEqual(payload["structured_outputs"], {"json": intent_schema()})
        self.assertNotIn("response_format", payload)
        self.assertNotIn("extra_body", payload)
        self.assertNotIn("guided_json", payload)
        with self.assertRaises(ProviderError):
            replace(config, schema_transport="guided-json")

    def test_endpoint_config_rejects_implicit_credentials_and_bad_options(self):
        for url in ("ftp://localhost/v1", "https://user:pass@host/v1", "https://host/v1?k=x",
                    "https://host/v1#fragment", "https://host", "https://host:99999/v1"):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                EndpointConfig(url, "model")
        for timeout in (0, -1, float("nan"), float("inf"), True, 301):
            with self.subTest(timeout=timeout), self.assertRaises(ProviderError):
                replace(self.config, timeout_s=timeout)
        for tokens in (0, 9000, True, 3.2):
            with self.subTest(tokens=tokens), self.assertRaises(ProviderError):
                replace(self.config, max_tokens=tokens)
        with self.assertRaises(ProviderError):
            replace(self.config, model="")
        with self.assertRaises(ProviderError):
            replace(self.config, api_key="private")
        secret_config = EndpointConfig("https://example.invalid/v1", "model", api_key="private")
        self.assertNotIn("private", repr(secret_config))

    def test_only_final_content_never_reasoning_fallback(self):
        self.assertEqual(extract_final_content(envelope(reasoning_content="ignore me")), STOP)
        for content in (None, "", " ", [], {"kind": "stop_request"}):
            with self.subTest(content=content), self.assertRaises(ProviderError):
                extract_final_content(envelope(content, reasoning_content=STOP, reasoning=STOP))

    def test_rejects_truncation_multiple_choices_tool_calls_and_refusals(self):
        for finish in ("length", "tool_calls", None, "content_filter"):
            row = envelope(); row["choices"][0]["finish_reason"] = finish
            with self.subTest(finish=finish), self.assertRaises(ProviderError):
                extract_final_content(row)
        for updates in ({"tool_calls": [{"id": "call"}]}, {"function_call": {"name": "move"}},
                        {"refusal": "no"}, {"role": "user"}, {"tool_calls": False},
                        {"tool_calls": {}}, {"function_call": {}}, {"refusal": False}):
            with self.subTest(updates=updates), self.assertRaises(ProviderError):
                extract_final_content(envelope(**updates))
        for value in ({}, [], dict(envelope(), error={"message": "private"}), {"choices": []}, {"choices": envelope()["choices"] * 2}):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                extract_final_content(value)

    def test_oversized_or_invalid_unicode_content_is_sanitized(self):
        for content in ("x" * (MAX_RESPONSE_BYTES + 1), "\ud800"):
            provider = InferenceProvider("json-object", self.config,
                                         transport=lambda *_: envelope(content))
            result = provider.generate("Stop.", CAPS)
            self.assertIsNotNone(result.error)
            self.assertIsNone(result.raw_content)

    def test_injected_transport_success_timeout_and_error(self):
        calls = []
        def fake(config, payload):
            calls.append(payload)
            return envelope()
        result = InferenceProvider("native-schema", self.config, transport=fake).generate("Stop.", CAPS)
        self.assertEqual(result.raw_content, STOP)
        self.assertEqual(result.repair_status, "not_applicable")
        self.assertEqual(len(result.request_sha256), 64)
        self.assertEqual(len(calls), 1)
        for error, timed_out in ((TimeoutError("secret"), True), (OSError("private key"), False)):
            with patch("robot_llm.providers.time.monotonic", side_effect=[1., 1.1]):
                result = InferenceProvider("json-object", self.config,
                    transport=MagicMock(side_effect=error)).generate("Stop.", CAPS)
            self.assertEqual(result.timed_out, timed_out)
            self.assertNotIn("secret", result.error)
            self.assertNotIn("private", result.error)

    def test_transport_never_follows_redirect_or_retries(self):
        conn = MagicMock(); conn.getresponse.return_value.status = 302
        with patch("robot_llm.providers.http.client.HTTPConnection", return_value=conn) as factory:
            with self.assertRaisesRegex(ProviderError, "302"):
                post_completion(replace(self.config, allow_network=True), {})
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(conn.request.call_count, 1)
            conn.close.assert_called_once()

    def test_transport_rejects_non_json_and_oversize_envelopes(self):
        for content_type, chunks in (("text/event-stream", []), ("application/json", [b"x" * (MAX_RESPONSE_BYTES+1)])):
            conn = MagicMock(); response = conn.getresponse.return_value
            response.status = 200; response.getheader.return_value = content_type
            response.read1.side_effect = chunks
            with patch("robot_llm.providers.http.client.HTTPConnection", return_value=conn):
                with self.assertRaises(ProviderError):
                    post_completion(replace(self.config, allow_network=True), {})
                conn.close.assert_called_once()

    def test_transport_rejects_duplicate_nonfinite_deep_or_invalid_json(self):
        for raw in (b'{"choices":[],"choices":[]}', b'{"x":NaN}', b'[',
                    b'[' * 1200 + b']' * 1200, b'\xff'):
            conn = MagicMock(); response = conn.getresponse.return_value
            response.status = 200; response.getheader.return_value = "application/json"
            response.read1.side_effect = [raw, b""]
            with self.subTest(raw=raw[:20]), patch("robot_llm.providers.http.client.HTTPConnection", return_value=conn):
                with self.assertRaises(ProviderError):
                    post_completion(replace(self.config, allow_network=True), {})

    def test_transport_serializes_plain_http_without_ambient_proxy(self):
        conn = MagicMock(); response = conn.getresponse.return_value
        response.status = 200; response.getheader.return_value = "application/json; charset=utf-8"
        response.read1.side_effect = [json.dumps(envelope()).encode(), b""]
        with patch("robot_llm.providers.http.client.HTTPConnection", return_value=conn):
            self.assertEqual(post_completion(replace(self.config, allow_network=True), {"stream": False}), envelope())
        args = conn.request.call_args.args
        self.assertEqual(args[0:2], ("POST", "/v1/chat/completions"))
        self.assertNotIn("Authorization", args[3])

    def test_optional_baml_does_not_load_in_default_mode(self):
        with patch("robot_llm.providers._load_baml", side_effect=AssertionError("unexpected dependency")):
            build_payload("json-object", self.config, "Stop.", CAPS)
            build_payload("native-schema", self.config, "Stop.", CAPS)

    def test_baml_trace_export_requires_isolated_environment(self):
        from robot_llm.providers import _load_baml
        with patch.dict("os.environ", {"BOUNDARY_API_KEY": "do-not-use"}):
            with self.assertRaisesRegex(ProviderError, "unset BOUNDARY_API_KEY"):
                _load_baml()


if __name__ == "__main__":
    unittest.main()

"""A service that answers only streamed (#23): a reply that comes as server-sent events is joined into one, a service
that refuses a request not streamed is asked again with "stream": true, and every other endpoint gets the request it
got before. A local server stands in for the services; no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from textfold import llm_openai, providers

REPLY = {"takeaway": "流式的回复也能读", "topic": "流式"}


def sse(text, usage=None):
    """text sent as a stream: a role chunk, the text in three pieces, an empty one, [DONE]."""
    n = len(text) // 3
    pieces = [text[:n], text[n:2 * n], text[2 * n:]]
    chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
    chunks += [{"choices": [{"index": 0, "delta": {"content": p}}]} for p in pieces]
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], **({"usage": usage} if usage else {})})
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"


class Service(BaseHTTPRequestHandler):
    """/plain answers as most endpoints do; /only takes a stream alone and says so; /mute takes a stream alone and
    says nothing of it; /always streams whatever it is asked."""
    seen = []

    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Service.seen.append((self.path, body))
        text = json.dumps(REPLY, ensure_ascii=False)
        if self.path.startswith("/plain"):
            return self.send(200, json.dumps({"choices": [{"message": {"content": text}}]}, ensure_ascii=False))
        if self.path.startswith("/always"):
            return self.send(200, sse(text), "text/event-stream; charset=utf-8")
        if self.path.startswith("/mislabelled"):
            # a whole completion, under the stream's header: some OpenAI-compatible relays answer so
            return self.send(200, json.dumps({"choices": [{"message": {"content": text}}]}, ensure_ascii=False),
                             "text/event-stream; charset=utf-8")
        if self.path.startswith("/comment"):
            # a stream that opens with a comment line, as a streaming service keeps the connection alive
            return self.send(200, ": keep-alive\n\n" + sse(text), "text/event-stream; charset=utf-8")
        if not body.get("stream"):
            said = {"error": {"message": "only stream=true is supported"}} if self.path.startswith("/only") else {"error": "bad request"}
            return self.send(400, json.dumps(said))
        return self.send(200, sse(text, {"total_tokens": 12}), "text/event-stream")


class Streamed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Service)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Service.seen.clear()
        llm_openai._WORKED.clear()

    def ask(self, path):
        spec = {"base": f"{self.base}/{path}", "model": "m", "api_key": "k", "provider": path}
        return llm_openai.call("Fold this.", {"type": "object"}, spec, "system", 10)

    def test_an_endpoint_that_answers_whole_is_asked_as_before(self):
        self.assertEqual(self.ask("plain"), (REPLY, None))
        self.assertEqual(len(Service.seen), 1)
        self.assertNotIn("stream", Service.seen[0][1])
        self.assertEqual(Service.seen[0][1]["response_format"], {"type": "json_object"})

    def test_a_service_that_says_it_takes_a_stream_alone(self):
        self.assertEqual(self.ask("only"), (REPLY, None))
        self.assertEqual([b.get("stream") for _, b in Service.seen], [None, True])   # straight to the stream
        self.ask("only")   # the way that worked is remembered
        self.assertEqual([b.get("stream") for _, b in Service.seen], [None, True, True])

    def test_a_service_that_takes_a_stream_alone_without_saying_so(self):
        self.assertEqual(self.ask("mute"), (REPLY, None))
        self.assertEqual([b.get("stream") for _, b in Service.seen], [None, None, True])

    def test_a_reply_streamed_unasked(self):
        self.assertEqual(self.ask("always"), (REPLY, None))
        self.assertEqual(len(Service.seen), 1)
        self.assertNotIn("stream", Service.seen[0][1])

    def test_a_whole_reply_under_the_streams_header(self):
        """A relay that keeps "text/event-stream" on a whole completion is read as one. Read as events it said nothing
        and the call failed "no JSON in reply", with the reply right there in the body."""
        self.assertEqual(self.ask("mislabelled"), (REPLY, None))
        self.assertEqual(len(Service.seen), 1)

    def test_a_stream_that_opens_with_a_comment_is_still_a_stream(self):
        """A stream keeps itself alive with a ": keep-alive" comment before its first data: line. The header's saying
        it is one is what _post() goes by: judged by the body alone, such a reply would look like neither."""
        self.assertEqual(self.ask("comment"), (REPLY, None))
        self.assertEqual(len(Service.seen), 1)


class Events(unittest.TestCase):
    def test_the_pieces_joined(self):
        d = llm_openai._events(sse('{"a": "第一 行"}', {"total_tokens": 3}).encode())
        self.assertEqual(llm_openai._content(d), '{"a": "第一 行"}')
        self.assertEqual(d["usage"], {"total_tokens": 3})

    def test_parts_comments_and_other_choices(self):
        raw = (": keep-alive\n\n"
               'data: {"choices": [{"index": 0, "delta": {"content": [{"type": "text", "text": "{\\"a\\""}]}}]}\n\n'
               'data: {"choices": [{"index": 1, "delta": {"content": "other"}}]}\n\n'
               'data: {"choices": [{"index": 0, "delta": {"content": ": 1}"}}]}\n\n'
               "data: [DONE]\n\n")
        self.assertEqual(llm_openai._content(llm_openai._events(raw.encode())), '{"a": 1}')

    def test_an_error_in_the_stream(self):
        d = llm_openai._events(b'data: {"error": {"message": "quota exceeded"}}\n\n')
        self.assertIsNone(llm_openai._content(d))
        self.assertEqual(d["error"]["message"], "quota exceeded")


class WorkBuddy(unittest.TestCase):
    """WorkBuddy Cloud, as its SDK (@tencent-ai/workbuddy-cloud-sdk 0.1.4) speaks to it: the publishable key in
    x-wb-webapp-access-key, no Authorization (a signed-in user's session token goes there), stream: true at once."""
    BASE = "https://demo.workbuddy.host/.cloud/llm"

    def setUp(self):
        llm_openai._WORKED.clear()

    def test_a_stream_asked_for_at_once_with_the_key_its_way(self):
        sent = []

        def post(url, body, headers, timeout):
            sent.append((url, body, headers))
            return llm_openai._events(sse(json.dumps(REPLY, ensure_ascii=False)).encode())

        spec = {"base": self.BASE, "model": "auto", "api_key": "pk_demo", "provider": "WorkBuddy"}
        with mock.patch.object(llm_openai, "_post", post):
            self.assertEqual(llm_openai.call("Fold this.", {"type": "object"}, spec, "system", 10), (REPLY, None))
        url, body, headers = sent[0]
        self.assertEqual((url, len(sent)), (f"{self.BASE}/chat/completions", 1))
        self.assertIs(body["stream"], True)
        self.assertEqual(body["messages"][0]["role"], "system")   # (its model hub wants the app's system prompt first)
        self.assertEqual(headers["x-wb-webapp-access-key"], "pk_demo")
        self.assertNotIn("Authorization", headers)

    def test_its_models_listed_with_the_key_its_way(self):
        seen = []

        def urlopen(req, timeout=None):
            seen.append(req)
            r = mock.MagicMock()
            r.__enter__.return_value.read.return_value = b'{"object": "list", "data": [{"id": "auto", "owned_by": "workbuddy"}]}'
            return r

        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual([m["id"] for m in providers.probe(self.BASE, "pk_demo")], ["auto"])
        self.assertEqual(seen[0].get_header("X-wb-webapp-access-key"), "pk_demo")
        self.assertFalse(seen[0].has_header("Authorization"))

    def test_every_other_endpoint_keeps_its_bearer(self):
        self.assertEqual(llm_openai.auth("https://api.deepseek.com/v1", "sk"), {"Authorization": "Bearer sk"})
        self.assertEqual(llm_openai.auth("https://workbuddy.host.example.com/v1", "sk"), {"Authorization": "Bearer sk"})


if __name__ == "__main__":
    unittest.main()

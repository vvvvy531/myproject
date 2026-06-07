#!/usr/bin/env python3
"""Local OpenAI-compatible shim for DeepWiki.

Embeddings are deterministic local vectors; chat completions are forwarded to the
configured OpenAI-compatible upstream.
"""
import argparse
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


def endpoint_url(base: str, endpoint: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith(endpoint) else base + endpoint


def embedding_for(text: str, dims: int = 256) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
    return [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(dims)]


class Handler(BaseHTTPRequestHandler):
    upstream_base = ""
    upstream_key = ""

    def log_message(self, fmt, *args):
        return

    def send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            request = self.read_json()
        except Exception:
            self.send_json(400, {"error": "invalid json"})
            return

        if self.path.endswith("/embeddings"):
            inputs = request.get("input", "")
            if isinstance(inputs, str):
                inputs = [inputs]
            dims = int(request.get("dimensions") or 256)
            data = [
                {"object": "embedding", "index": idx, "embedding": embedding_for(str(text), dims)}
                for idx, text in enumerate(inputs)
            ]
            self.send_json(200, {
                "object": "list",
                "model": request.get("model", "local-deterministic-embedding"),
                "data": data,
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            })
            return

        if self.path.endswith("/chat/completions"):
            url = endpoint_url(self.upstream_base, "/chat/completions")
            headers = {
                "Authorization": f"Bearer {self.upstream_key}",
                "Content-Type": "application/json",
            }
            stream = bool(request.get("stream"))
            try:
                response = requests.post(url, headers=headers, json=request, timeout=600, stream=stream)
            except Exception as exc:
                self.send_json(502, {"error": f"upstream request failed: {exc}"})
                return

            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.end_headers()
            if stream:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()
            else:
                self.wfile.write(response.content)
            return

        self.send_json(404, {"error": "not found"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    Handler.upstream_base = os.environ["UPSTREAM_OPENAI_BASE_URL"]
    Handler.upstream_key = os.environ["UPSTREAM_OPENAI_API_KEY"]
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"OpenAI-compatible shim listening on http://{args.host}:{args.port}/v1", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
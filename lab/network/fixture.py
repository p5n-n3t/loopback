"""Owned lab HTTP fixture. No production provider or credential is used."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = min(int(self.headers.get("Content-Length", "0")), 8192)
        request = json.loads(self.rfile.read(length) or "{}")
        mode = os.environ.get("LAB_MODE", "mcp")
        if mode == "auth":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer resource_metadata="https://fixture.invalid/metadata"')
            self.end_headers()
            return
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()
            return
        body = (
            {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "lab-network-fixture", "version": "1"}},
            }
            if mode == "mcp"
            else {"service": "ordinary-json"}
        )
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()

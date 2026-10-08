import http.server
import json
import threading

import pytest


class Recorder(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        srv = self.server
        srv.calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
        code, reply = srv.script.pop(0) if srv.script else (200, b"{}")
        self.send_response(code)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


@pytest.fixture
def http_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    srv.calls, srv.script = [], []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()

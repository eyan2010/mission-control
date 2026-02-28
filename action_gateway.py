#!/usr/bin/env python3
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.getenv("ACTION_GATEWAY_HOST", "0.0.0.0")
PORT = int(os.getenv("ACTION_GATEWAY_PORT", "8787"))
TOKEN = os.getenv("ACTION_GATEWAY_TOKEN", "")
ALLOWED_ORIGIN = os.getenv("ACTION_GATEWAY_ORIGIN", "https://eyan2010.github.io")
ALLOWLIST = {x.strip() for x in os.getenv("ACTION_GATEWAY_CRON_ALLOWLIST", "").split(",") if x.strip()}
LOG_PATH = os.getenv("ACTION_GATEWAY_LOG", "/tmp/mission-control-action-gateway.log")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, code, payload):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_action(self, payload):
        line = {"ts": int(time.time()), **payload}
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/dashboard-action":
            return self._json(404, {"ok": False, "error": "not_found"})

        if TOKEN:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {TOKEN}":
                return self._json(401, {"ok": False, "error": "unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8") if body else "{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "invalid_json"})

        if data.get("action") != "cron.run":
            return self._json(400, {"ok": False, "error": "unsupported_action"})

        cron_name = (data.get("cronName") or "").strip()
        if not cron_name:
            return self._json(400, {"ok": False, "error": "cron_name_required"})

        if ALLOWLIST and cron_name not in ALLOWLIST:
            return self._json(403, {"ok": False, "error": "cron_not_allowlisted"})

        try:
            p = subprocess.run(
                ["openclaw", "cron", "list", "--all", "--json"],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            jobs = json.loads(p.stdout).get("jobs", [])
        except Exception as e:
            self.log_action({"ok": False, "cronName": cron_name, "error": f"list_failed:{e}"})
            return self._json(500, {"ok": False, "error": "cron_list_failed"})

        job = next((j for j in jobs if j.get("name") == cron_name), None)
        if not job:
            return self._json(404, {"ok": False, "error": "cron_not_found"})

        job_id = job.get("jobId") or job.get("id")
        if not job_id:
            return self._json(500, {"ok": False, "error": "cron_id_missing"})

        try:
            r = subprocess.run(
                ["openclaw", "cron", "run", str(job_id)],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            self.log_action({"ok": True, "cronName": cron_name, "jobId": job_id, "stdout": r.stdout[:400]})
            return self._json(200, {"ok": True, "cronName": cron_name, "jobId": job_id})
        except subprocess.CalledProcessError as e:
            self.log_action({"ok": False, "cronName": cron_name, "jobId": job_id, "stderr": (e.stderr or "")[:400]})
            return self._json(500, {"ok": False, "error": "cron_run_failed", "details": (e.stderr or "")[:200]})


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Mission Control action gateway listening on http://{HOST}:{PORT}")
    server.serve_forever()

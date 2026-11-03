"""Serve the dashboard and evaluate the next 15 CRM notes locally."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "generated"


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/api/evaluate-next-batch":
            self.send_error(404)
            return
        existing = DATA / "note_extraction_results.csv"
        count = max(0, sum(1 for _ in existing.open(encoding="utf-8")) - 1) if existing.exists() else 0
        total = max(0, sum(1 for _ in (DATA / "crm_case_manager_notes.csv").open(encoding="utf-8")) - 1)
        if count >= total:
            self._json({"status": "complete", "message": f"All {total:,} labelled notes have been evaluated."})
            return
        command = [
            sys.executable,
            str(ROOT / "extract_note_signals.py"),
            "--input-dir", str(DATA),
            "--output-dir", str(DATA),
            "--sample-size", "15",
            "--offset", str(count),
            "--sleep-seconds", "5",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1500)
        if completed.returncode:
            status_path = DATA / "note_extraction_status.json"
            payload = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {
                "status": "error",
                "message": completed.stderr[-2000:],
            }
            self._json(payload, 429 if payload.get("status") == "quota_limit" else 500)
            return
        self._json({
            "status": "complete",
            "message": f"Evaluated the next 15 notes. Total evaluated: {min(count + 15, total):,} of {total:,}.",
        })

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    ThreadingHTTPServer(("localhost", port), Handler).serve_forever()

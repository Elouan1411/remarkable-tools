#!/usr/bin/env python3
"""Local HTTP server and REST API for the reMarkable USB Backup Web GUI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from backup import (
    BASE_URL,
    FORMAT_EXTENSIONS,
    check_connection,
    clear_skipped,
    collect_documents_from_tree,
    fetch_tree,
    load_skipped,
    run_backup,
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_WEB_OUTPUT_DIR = APP_DIR / "export_remarkable"
DEFAULT_WEB_SKIP_FILE = APP_DIR / "skipped.txt"


def _count_tree_items(tree: list[dict[str, Any]]) -> tuple[int, int]:
    """Count (folders, documents) recursively in a tree structure."""
    folders = 0
    documents = 0
    for node in tree:
        if node["type"] == "folder":
            folders += 1
            sub_folders, sub_docs = _count_tree_items(node.get("children", []))
            folders += sub_folders
            documents += sub_docs
        elif node["type"] == "document":
            documents += 1
    return folders, documents


class BackupStateManager:
    """Thread-safe coordinator for tablet tree cache and active backup jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tree_lock = threading.Lock()
        self._cached_tree: list[dict[str, Any]] | None = None
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._logs: list[dict[str, str]] = []
        self._status: dict[str, Any] = {
            "state": "idle",
            "current": 0,
            "total": 0,
            "current_file": "",
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "total_mb": 0.0,
            "output_dir": str(DEFAULT_WEB_OUTPUT_DIR),
            "export_format": "pdf",
            "error": None,
        }

    def get_tree(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Return cached tablet tree or fetch a fresh one from the tablet."""
        with self._tree_lock:
            if refresh or self._cached_tree is None:
                self._cached_tree = fetch_tree(base_url=BASE_URL)
            return self._cached_tree

    def append_log(self, level: str, message: str) -> None:
        """Append a timestamped log entry to the in-memory log buffer."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._logs.append({"time": timestamp, "level": level, "message": message})
            if len(self._logs) > 2000:
                self._logs = self._logs[-2000:]

    def start_backup(
        self,
        selected_doc_ids: list[str] | None,
        export_format: str,
        output_dir_str: str,
        skip_existing: bool,
        use_skip_list: bool,
        timeout: int = 90,
    ) -> tuple[bool, str | int]:
        """Validate inputs and launch a background backup thread."""
        if export_format not in FORMAT_EXTENSIONS:
            return False, f"Invalid format '{export_format}'. Must be pdf, rmdoc, or both."

        with self._lock:
            if self._status["state"] == "running":
                return False, "A backup job is already running."

        try:
            tree = self.get_tree(refresh=False)
        except requests.exceptions.ConnectionError:
            return (
                False,
                "Cannot reach reMarkable tablet at http://10.11.99.1. "
                "Verify USB connection and Settings > Storage > USB web interface.",
            )

        selected_set = set(selected_doc_ids) if selected_doc_ids is not None else None
        documents = collect_documents_from_tree(tree, selected_doc_ids=selected_set)
        if not documents:
            return False, "No matching documents selected for backup."

        raw_out = (output_dir_str or "").strip()
        output_dir = (
            Path(raw_out).expanduser() if raw_out else DEFAULT_WEB_OUTPUT_DIR
        )
        if not output_dir.is_absolute():
            output_dir = (APP_DIR / output_dir).resolve()

        total_tasks = len(documents) * len(FORMAT_EXTENSIONS[export_format])

        with self._lock:
            self._stop_event.clear()
            self._logs = []
            self._status = {
                "state": "running",
                "current": 0,
                "total": total_tasks,
                "current_file": "",
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "total_mb": 0.0,
                "output_dir": str(output_dir),
                "export_format": export_format,
                "error": None,
            }

        def on_progress(progress_data: dict[str, Any]) -> None:
            with self._lock:
                self._status.update(progress_data)

        def worker() -> None:
            try:
                result = run_backup(
                    documents=documents,
                    export_format=export_format,
                    output_dir=output_dir,
                    skip_file=DEFAULT_WEB_SKIP_FILE,
                    skip_existing=skip_existing,
                    use_skip_list=use_skip_list,
                    timeout=timeout,
                    base_url=BASE_URL,
                    stop_event=self._stop_event,
                    on_progress=on_progress,
                    on_log=self.append_log,
                )
                with self._lock:
                    self._status["state"] = (
                        "cancelled" if result.cancelled else "completed"
                    )
                    self._status["current"] = result.total_tasks if not result.cancelled else self._status["current"]
                    self._status["downloaded"] = result.downloaded
                    self._status["skipped"] = (
                        result.skipped_existing + result.skipped_blacklist
                    )
                    self._status["failed"] = result.failed
                    self._status["total_mb"] = round(result.total_mb, 2)
                self.append_log(
                    "warning" if result.cancelled else "success",
                    (
                        f"Backup cancelled. {result.downloaded} saved ({result.total_mb:.2f} MB)."
                        if result.cancelled
                        else f"Backup completed! {result.downloaded} downloaded ({result.total_mb:.2f} MB), "
                        f"{result.skipped_existing + result.skipped_blacklist} skipped, {result.failed} failed."
                    ),
                )
            except requests.exceptions.ConnectionError:
                msg = "Lost connection to the tablet at http://10.11.99.1."
                self.append_log("error", msg)
                with self._lock:
                    self._status["state"] = "error"
                    self._status["error"] = msg
            except Exception as exc:
                msg = f"Unexpected backup error: {exc}"
                self.append_log("error", msg)
                with self._lock:
                    self._status["state"] = "error"
                    self._status["error"] = msg

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()
        return True, len(documents)

    def cancel_backup(self) -> bool:
        """Signal the active backup worker to stop."""
        with self._lock:
            if self._status["state"] != "running":
                return False
            self._stop_event.set()
        self.append_log("warning", "Cancellation requested, stopping after current chunk...")
        return True

    def get_progress(self, since: int = 0) -> dict[str, Any]:
        """Return current progress state and incremental log entries."""
        skipped_files = sorted(load_skipped(DEFAULT_WEB_SKIP_FILE))
        with self._lock:
            safe_since = max(0, min(since, len(self._logs)))
            return {
                **self._status,
                "logs": self._logs[safe_since:],
                "next_log_index": len(self._logs),
                "skipped_count": len(skipped_files),
                "skipped_files": skipped_files,
            }


STATE_MANAGER = BackupStateManager()


class BackupHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving static GUI assets and JSON API routes."""

    server_version = "RemarkableBackupGUI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy per-request polling logs in terminal
        pass

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _serve_static(self, rel_path: str) -> None:
        clean = rel_path.lstrip("/") or "index.html"
        target = (STATIC_DIR / clean).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        content_type, _ = mimetypes.guess_type(str(target))
        if not content_type:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type = f"{content_type}; charset=utf-8"

        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            connected = check_connection(base_url=BASE_URL, timeout=2)
            skipped_files = sorted(load_skipped(DEFAULT_WEB_SKIP_FILE))
            progress = STATE_MANAGER.get_progress(since=0)
            self._send_json(
                {
                    "connected": connected,
                    "base_url": BASE_URL,
                    "default_output_dir": str(DEFAULT_WEB_OUTPUT_DIR),
                    "skip_file": str(DEFAULT_WEB_SKIP_FILE),
                    "skipped_count": len(skipped_files),
                    "skipped_files": skipped_files,
                    "backup_state": progress["state"],
                }
            )
            return

        if path == "/api/tree":
            query = parse_qs(parsed.query)
            refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
            try:
                tree = STATE_MANAGER.get_tree(refresh=refresh)
                folder_count, doc_count = _count_tree_items(tree)
                self._send_json(
                    {
                        "connected": True,
                        "tree": tree,
                        "folder_count": folder_count,
                        "doc_count": doc_count,
                    }
                )
            except requests.exceptions.ConnectionError:
                self._send_json(
                    {
                        "connected": False,
                        "error": (
                            "Could not reach the reMarkable tablet at http://10.11.99.1. "
                            "Connect the USB cable and enable Settings > Storage > USB web interface."
                        ),
                    },
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            except Exception as exc:
                self._send_json(
                    {"connected": False, "error": f"Failed to read tablet tree: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if path == "/api/backup/progress":
            query = parse_qs(parsed.query)
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(STATE_MANAGER.get_progress(since=since))
            return

        if path == "/" or not path.startswith("/api/"):
            self._serve_static(path)
            return

        self._send_json({"error": "Endpoint not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/backup/start":
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return

            selected_doc_ids = body.get("selected_doc_ids")
            if selected_doc_ids is not None and not isinstance(selected_doc_ids, list):
                self._send_json(
                    {"error": "'selected_doc_ids' must be a list of IDs."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            if isinstance(selected_doc_ids, list) and len(selected_doc_ids) == 0:
                self._send_json(
                    {"error": "Please select at least one folder or document."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            export_format = str(body.get("export_format", "pdf")).lower()
            output_dir = str(body.get("output_dir", str(DEFAULT_WEB_OUTPUT_DIR)))
            skip_existing = bool(body.get("skip_existing", True))
            use_skip_list = bool(body.get("use_skip_list", True))
            try:
                timeout = max(10, int(body.get("timeout", 90)))
            except (TypeError, ValueError):
                timeout = 90

            ok, info = STATE_MANAGER.start_backup(
                selected_doc_ids=selected_doc_ids,
                export_format=export_format,
                output_dir_str=output_dir,
                skip_existing=skip_existing,
                use_skip_list=use_skip_list,
                timeout=timeout,
            )
            if not ok:
                status = (
                    HTTPStatus.CONFLICT
                    if "already running" in str(info)
                    else HTTPStatus.BAD_REQUEST
                )
                self._send_json({"error": info}, status=status)
                return

            self._send_json(
                {"status": "started", "total_documents": info},
                status=HTTPStatus.ACCEPTED,
            )
            return

        if path == "/api/backup/cancel":
            cancelled = STATE_MANAGER.cancel_backup()
            if not cancelled:
                self._send_json(
                    {"error": "No backup is currently running."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"status": "cancelling"})
            return

        if path == "/api/skipped/clear":
            clear_skipped(DEFAULT_WEB_SKIP_FILE)
            self._send_json({"status": "cleared", "skipped_count": 0, "skipped_files": []})
            return

        self._send_json({"error": "Endpoint not found"}, status=HTTPStatus.NOT_FOUND)


def create_server(host: str = "127.0.0.1", port: int = 8080) -> tuple[ThreadingHTTPServer, int]:
    """Bind ThreadingHTTPServer to the requested port or next available port."""
    for candidate in range(port, port + 15):
        try:
            server = ThreadingHTTPServer((host, candidate), BackupHTTPHandler)
            return server, candidate
        except OSError:
            continue
    raise OSError(f"Could not bind HTTP server on ports {port}-{port + 14}.")


def serve_gui(
    host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True
) -> None:
    """Start the local HTTP server and optionally open the browser."""
    server, bound_port = create_server(host=host, port=port)
    url = f"http://{host}:{bound_port}"
    print(f"reMarkable USB Backup Web GUI running at {url}", flush=True)
    print("Press Ctrl+C to stop the server.", flush=True)

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...", flush=True)
    finally:
        server.server_close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the Web GUI server."""
    parser = argparse.ArgumentParser(
        description="Launch the Web GUI for reMarkable USB Backup."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to serve the Web GUI on (default: 8080).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the default web browser.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    serve_gui(host=args.host, port=args.port, open_browser=not args.no_browser)

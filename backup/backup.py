#!/usr/bin/env python3
"""Core backup engine and CLI for the reMarkable USB Web Interface."""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

BASE_URL = "http://10.11.99.1"
DEFAULT_OUTPUT_DIR = Path("export_remarkable")
DEFAULT_SKIP_FILE = Path("skipped.txt")

FORMAT_EXTENSIONS: dict[str, list[tuple[str, str]]] = {
    "pdf": [("pdf", ".pdf")],
    "rmdoc": [("rmdoc", ".rmdoc")],
    "both": [("pdf", ".pdf"), ("rmdoc", ".rmdoc")],
}


class BackupCancelledError(Exception):
    """Raised when a running backup operation is cancelled by the user."""


@dataclass
class BackupResult:
    """Summary statistics produced by a backup run."""

    total_tasks: int = 0
    downloaded: int = 0
    skipped_existing: int = 0
    skipped_blacklist: int = 0
    failed: int = 0
    total_mb: float = 0.0
    cancelled: bool = False
    failed_files: list[str] = field(default_factory=list)


def sanitize_filename(name: str) -> str:
    """Sanitize a document or folder name for local filesystem paths."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    return cleaned or "unnamed"


def load_skipped(skip_file: Path = DEFAULT_SKIP_FILE) -> set[str]:
    """Load relative file paths listed in the skip/blacklist file."""
    if skip_file.exists():
        return {
            line.strip()
            for line in skip_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    return set()


def add_to_skipped(rel_path: str, skip_file: Path = DEFAULT_SKIP_FILE) -> None:
    """Append a relative file path to the skip/blacklist file."""
    skip_file.parent.mkdir(parents=True, exist_ok=True)
    with open(skip_file, "a", encoding="utf-8") as f:
        f.write(f"{rel_path}\n")


def clear_skipped(skip_file: Path = DEFAULT_SKIP_FILE) -> None:
    """Clear all entries from the skip/blacklist file."""
    if skip_file.exists():
        skip_file.unlink()


def check_connection(base_url: str = BASE_URL, timeout: int = 3) -> bool:
    """Return True if the reMarkable USB web interface responds at base_url."""
    try:
        response = requests.get(f"{base_url.rstrip('/')}/documents/", timeout=timeout)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def wait_for_tablet_ready(
    base_url: str = BASE_URL,
    stop_event: threading.Event | None = None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """Poll the tablet web server until it responds with HTTP 200."""
    if on_status:
        on_status("Tablet busy, waiting for current rendering to finish...")
    else:
        print(
            "   ⏳ Tablet busy, waiting for current operation to finish...",
            end="",
            flush=True,
        )

    url = f"{base_url.rstrip('/')}/documents/"
    while True:
        if stop_event and stop_event.is_set():
            raise BackupCancelledError("Backup cancelled while waiting for tablet.")
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                if on_status:
                    on_status("Tablet is ready.")
                else:
                    print(" ready!", flush=True)
                return
        except requests.exceptions.RequestException:
            pass

        if not on_status:
            print(".", end="", flush=True)

        if stop_event:
            if stop_event.wait(5):
                raise BackupCancelledError("Backup cancelled while waiting for tablet.")
        else:
            time.sleep(5)


def fetch_items(
    folder_id: str = "",
    base_url: str = BASE_URL,
    stop_event: threading.Event | None = None,
    on_status: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Fetch items inside a folder (or root if folder_id is empty)."""
    root_url = base_url.rstrip("/")
    url = f"{root_url}/documents/{folder_id}" if folder_id else f"{root_url}/documents/"

    while True:
        if stop_event and stop_event.is_set():
            raise BackupCancelledError("Backup cancelled while reading tablet tree.")
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.json()
        except (requests.exceptions.HTTPError, requests.exceptions.Timeout):
            wait_for_tablet_ready(
                base_url=base_url, stop_event=stop_event, on_status=on_status
            )


def _deduplicate_name(base_name: str, item_id: str, seen: set[str]) -> str:
    """Prevent sibling collisions if two items sanitize to the same filename."""
    candidate = base_name
    if candidate.lower() in seen:
        suffix = item_id[:6] if item_id else "dup"
        candidate = f"{base_name}_{suffix}"
    seen.add(candidate.lower())
    return candidate


def fetch_tree(
    folder_id: str = "",
    rel_path: str = "",
    base_url: str = BASE_URL,
    stop_event: threading.Event | None = None,
    on_status: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Recursively build the folder and document hierarchy from the tablet."""
    items = fetch_items(
        folder_id=folder_id,
        base_url=base_url,
        stop_event=stop_event,
        on_status=on_status,
    )

    folders: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Sort raw items deterministically before resolving name collisions
    sorted_items = sorted(
        items,
        key=lambda x: (
            0 if x.get("Type") == "CollectionType" else 1,
            (x.get("VisibleName") or x.get("VissibleName") or x.get("ID") or "").lower(),
        ),
    )

    for item in sorted_items:
        item_id = item.get("ID", "")
        raw_name = item.get("VisibleName") or item.get("VissibleName") or item_id
        safe_name = _deduplicate_name(sanitize_filename(raw_name), item_id, seen_names)
        item_type = item.get("Type")
        item_rel_path = f"{rel_path}/{safe_name}" if rel_path else safe_name

        if item_type == "CollectionType":
            children = fetch_tree(
                folder_id=item_id,
                rel_path=item_rel_path,
                base_url=base_url,
                stop_event=stop_event,
                on_status=on_status,
            )
            doc_count = sum(
                child["doc_count"] if child["type"] == "folder" else 1
                for child in children
            )
            folders.append(
                {
                    "id": item_id,
                    "name": safe_name,
                    "raw_name": raw_name,
                    "type": "folder",
                    "modified": item.get("ModifiedClient", ""),
                    "rel_path": item_rel_path,
                    "doc_count": doc_count,
                    "children": children,
                }
            )
        elif item_type == "DocumentType":
            documents.append(
                {
                    "id": item_id,
                    "name": safe_name,
                    "raw_name": raw_name,
                    "type": "document",
                    "file_type": item.get("fileType", "notebook"),
                    "modified": item.get("ModifiedClient", ""),
                    "rel_path": item_rel_path,
                    "doc_count": 1,
                }
            )

    return folders + documents


def collect_documents_from_tree(
    tree: list[dict[str, Any]],
    selected_doc_ids: set[str] | None = None,
    selected_folder_ids: set[str] | None = None,
    selected_folder_paths: set[str] | None = None,
    _folder_matched: bool = False,
) -> list[dict[str, Any]]:
    """Flatten a hierarchical tree into a list of document entries matching filters."""
    docs: list[dict[str, Any]] = []
    has_filters = any(
        f is not None for f in (selected_doc_ids, selected_folder_ids, selected_folder_paths)
    )

    for node in tree:
        if node["type"] == "folder":
            matched_here = _folder_matched
            if not matched_here and selected_folder_ids and node["id"] in selected_folder_ids:
                matched_here = True
            if not matched_here and selected_folder_paths:
                norm_rel = node["rel_path"].strip("/").lower()
                if norm_rel in selected_folder_paths or node["name"].lower() in selected_folder_paths:
                    matched_here = True

            docs.extend(
                collect_documents_from_tree(
                    node.get("children", []),
                    selected_doc_ids=selected_doc_ids,
                    selected_folder_ids=selected_folder_ids,
                    selected_folder_paths=selected_folder_paths,
                    _folder_matched=matched_here,
                )
            )
        elif node["type"] == "document":
            if not has_filters or _folder_matched or (
                selected_doc_ids is not None and node["id"] in selected_doc_ids
            ):
                docs.append(node)

    return docs


def download_document(
    doc_id: str,
    dest_path: Path,
    fmt: str = "pdf",
    timeout: int = 90,
    base_url: str = BASE_URL,
    stop_event: threading.Event | None = None,
) -> float:
    """Download a document as 'pdf' or 'rmdoc' and return its size in megabytes."""
    if fmt not in ("pdf", "rmdoc"):
        raise ValueError(f"Unsupported format: {fmt}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_name(f"{dest_path.name}.part")
    url = f"{base_url.rstrip('/')}/download/{doc_id}/{fmt}"
    total_bytes = 0

    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with open(part_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if stop_event and stop_event.is_set():
                        raise BackupCancelledError("Download cancelled by user.")
                    if chunk:
                        f.write(chunk)
                        total_bytes += len(chunk)

        part_path.replace(dest_path)
    except Exception:
        if part_path.exists():
            part_path.unlink()
        raise

    return total_bytes / (1024 * 1024)


def run_backup(
    documents: list[dict[str, Any]],
    export_format: str = "pdf",
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    skip_file: Path = DEFAULT_SKIP_FILE,
    skip_existing: bool = True,
    use_skip_list: bool = True,
    timeout: int = 90,
    base_url: str = BASE_URL,
    stop_event: threading.Event | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str, str], None] | None = None,
) -> BackupResult:
    """Download the provided documents in the requested format(s)."""
    if export_format not in FORMAT_EXTENSIONS:
        raise ValueError(f"Invalid export format '{export_format}'. Choose pdf, rmdoc, or both.")

    targets: list[dict[str, Any]] = []
    for doc in documents:
        for fmt, ext in FORMAT_EXTENSIONS[export_format]:
            rel_path = f"{doc['rel_path']}{ext}"
            targets.append(
                {
                    "id": doc["id"],
                    "name": doc["name"],
                    "fmt": fmt,
                    "rel_path": rel_path,
                    "dest": output_dir / rel_path,
                }
            )

    result = BackupResult(total_tasks=len(targets))
    skipped_set = load_skipped(skip_file) if use_skip_list else set()

    def emit_log(level: str, msg: str) -> None:
        if on_log:
            on_log(level, msg)
        else:
            print(msg, flush=True)

    def emit_progress(current_idx: int, current_file: str) -> None:
        if on_progress:
            on_progress(
                {
                    "current": current_idx,
                    "total": result.total_tasks,
                    "current_file": current_file,
                    "downloaded": result.downloaded,
                    "skipped": result.skipped_existing + result.skipped_blacklist,
                    "failed": result.failed,
                    "total_mb": round(result.total_mb, 2),
                }
            )

    if result.total_tasks == 0:
        emit_log("warning", "No documents selected for export.")
        return result

    emit_log(
        "info",
        f"Starting {export_format.upper()} export for {len(documents)} document(s) "
        f"({result.total_tasks} file task(s)) into '{output_dir}'.",
    )

    for index, task in enumerate(targets, start=1):
        if stop_event and stop_event.is_set():
            result.cancelled = True
            emit_log("warning", "Backup cancelled by user.")
            break

        rel_path = task["rel_path"]
        dest_path: Path = task["dest"]
        emit_progress(index - 1, rel_path)

        if use_skip_list and rel_path in skipped_set:
            result.skipped_blacklist += 1
            emit_log("skip", f"[{index}/{result.total_tasks}] Skipped (skip list): {rel_path}")
            emit_progress(index, rel_path)
            continue

        if skip_existing and dest_path.exists() and dest_path.stat().st_size > 0:
            result.skipped_existing += 1
            emit_log("skip", f"[{index}/{result.total_tasks}] Already exists: {rel_path}")
            emit_progress(index, rel_path)
            continue

        emit_log(
            "info",
            f"[{index}/{result.total_tasks}] Downloading ({task['fmt']}) {rel_path}...",
        )
        try:
            size_mb = download_document(
                doc_id=task["id"],
                dest_path=dest_path,
                fmt=task["fmt"],
                timeout=timeout,
                base_url=base_url,
                stop_event=stop_event,
            )
            result.downloaded += 1
            result.total_mb += size_mb
            emit_log(
                "success",
                f"[{index}/{result.total_tasks}] Saved {rel_path} ({size_mb:.2f} MB)",
            )
            emit_progress(index, rel_path)
            if stop_event:
                if stop_event.wait(0.25 if task["fmt"] == "rmdoc" else 0.5):
                    result.cancelled = True
                    emit_log("warning", "Backup cancelled by user.")
                    break
            else:
                time.sleep(0.25 if task["fmt"] == "rmdoc" else 0.5)
        except BackupCancelledError:
            result.cancelled = True
            emit_log("warning", "Backup cancelled by user.")
            break
        except Exception as exc:
            result.failed += 1
            result.failed_files.append(rel_path)
            if dest_path.exists() and dest_path.stat().st_size == 0:
                dest_path.unlink()
            if use_skip_list:
                add_to_skipped(rel_path, skip_file)
                skipped_set.add(rel_path)
            emit_log(
                "error",
                f"[{index}/{result.total_tasks}] Failed {rel_path} ({exc})"
                + (f" -> added to {skip_file}" if use_skip_list else ""),
            )
            emit_progress(index, rel_path)
            try:
                wait_for_tablet_ready(
                    base_url=base_url,
                    stop_event=stop_event,
                    on_status=lambda s: emit_log("info", s),
                )
            except BackupCancelledError:
                result.cancelled = True
                emit_log("warning", "Backup cancelled by user.")
                break

    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for the reMarkable backup CLI."""
    parser = argparse.ArgumentParser(
        description="Back up documents from a reMarkable tablet over the USB Web Interface."
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the web-based graphical interface instead of running in CLI mode.",
    )
    parser.add_argument(
        "--format",
        choices=["pdf", "rmdoc", "both"],
        default="pdf",
        help="Export format: 'pdf' (flattened PDF), 'rmdoc' (fast native archive), or 'both'.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--folder",
        action="append",
        dest="folders",
        help="Folder path or folder ID to export (can be repeated). Exports all folders if omitted.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download files even if they already exist locally.",
    )
    parser.add_argument(
        "--ignore-skipped",
        action="store_true",
        help="Ignore entries in skipped.txt and retry previously failed files.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Per-file download timeout in seconds (default: 90).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for the web GUI when --gui is passed (default: 8080).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for reMarkable USB backup."""
    args = parse_args(argv)

    if args.gui:
        from app import serve_gui

        serve_gui(port=args.port, open_browser=True)
        return 0

    print("🔍 Scanning document tree on the reMarkable tablet...", flush=True)
    try:
        tree = fetch_tree()
    except requests.exceptions.ConnectionError:
        print("\n❌ Could not connect to the tablet at http://10.11.99.1.")
        print("Check the USB cable and enable Settings > Storage > USB web interface.")
        return 1

    folder_ids: set[str] | None = None
    folder_paths: set[str] | None = None
    if args.folders:
        folder_ids = set(args.folders)
        folder_paths = {f.strip("/").lower() for f in args.folders}

    documents = collect_documents_from_tree(
        tree,
        selected_folder_ids=folder_ids,
        selected_folder_paths=folder_paths,
    )

    if not documents:
        print("No matching documents found on the tablet.")
        return 0

    skip_file = DEFAULT_SKIP_FILE
    skipped_count = len(load_skipped(skip_file))
    print(
        f"📦 Found {len(documents)} document(s) "
        f"({skipped_count} entry/entries in {skip_file}).\n"
    )

    result = run_backup(
        documents=documents,
        export_format=args.format,
        output_dir=args.output,
        skip_file=skip_file,
        skip_existing=not args.overwrite,
        use_skip_list=not args.ignore_skipped,
        timeout=args.timeout,
    )

    print(f"\n🎉 Export finished! Files saved to: {args.output.resolve()}")
    print(
        f"   Downloaded: {result.downloaded} ({result.total_mb:.2f} MB) | "
        f"Skipped existing: {result.skipped_existing} | "
        f"Blacklisted: {result.skipped_blacklist} | "
        f"Failed: {result.failed}"
    )

    if result.failed_files:
        print(f"\n⚠️  {len(result.failed_files)} file(s) failed during this run:")
        for failed_path in result.failed_files:
            print(f"   - {failed_path}")

    return 1 if result.failed > 0 and result.downloaded == 0 else 0


if __name__ == "__main__":
    sys.exit(main())

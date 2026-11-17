# reMarkable USB Backup

An intuitive Web GUI and CLI utility to back up folders and documents from a **reMarkable** tablet while preserving the full directory hierarchy.

Unlike the built-in reMarkable web interface—which only lets you download one document at a time—this tool lets you **select specific folders or individual files** (or back up the entire tablet) in **PDF**, **reMarkable native (`.rmdoc`)**, or **both** formats.

Because it exclusively calls the **official USB Web Interface API** (`http://10.11.99.1`), it never modifies your tablet's internal filesystem and carries **zero risk of corrupting tablet data**.

---

## Features

- **Web Graphical Interface (`app.py`)**: Clean HTML/CSS/JS UI served locally by Python.
- **Selective Folder & Document Tree**: Browse your tablet's live hierarchy, search by name, toggle a **Folders Only** view, and select or deselect entire folders with cascading tri-state checkboxes.
- **Multiple Export Formats**:
  - **`.pdf`**: Standard flattened PDF with handwritten annotations rendered inside. Ideal for reading and sharing anywhere.
  - **`.rmdoc`**: Proprietary reMarkable archive format. **Much faster to export** (skips on-device PDF rendering) and preserves **layers, vector strokes, and metadata**, allowing you to re-import backups back onto your tablet for further editing.
  - **Both (`.pdf` + `.rmdoc`)**: Export both formats side by side into the same folder tree.
- **Resilient & Resumable**:
  - Preserves your exact folder structure locally.
  - Skips already-downloaded files automatically.
  - Downloads atomically via `.part` temporary files so interrupted transfers never leave corrupted files.
  - Automatically waits and retries if the tablet is temporarily busy rendering a large document.
  - Records timed-out / oversized exports in `skipped.txt` so subsequent runs can continue smoothly (and lets you clear `skipped.txt` anytime from the UI or CLI).
- **CLI Mode (`backup.py`)**: Scriptable command-line interface for automated or headless backups.

---

## Step 1: Enable USB Web Interface on Your Tablet

Before running the tool, enable the official USB interface on your reMarkable:

1. Connect your reMarkable tablet to your computer with a **USB cable**.
2. On your reMarkable, open **Settings &rarr; Storage**.
3. Enable **USB web interface**.
4. You can verify the connection by opening `http://10.11.99.1` in a browser (it should display reMarkable's built-in file list).

> **Note:** Keep the tablet awake or ensure USB web interface stays enabled during long PDF exports.

---

## Step 2: Installation (`venvPython`)

From the root of the `remarkable-tools` repository:

```bash
python3 -m venv venvPython
```

Activate the virtual environment depending on your shell:

- **Bash / Zsh:**
  ```bash
  source venvPython/bin/activate
  ```
- **Fish:**
  ```fish
  source venvPython/bin/activate.fish
  ```

Then install the Python dependencies:

```bash
pip install -r backup/requirements.txt
```

---

## Step 3: Usage

### Option A — Web Graphical Interface (Recommended)

Start the local Python web server:

```bash
python backup/app.py
```

Or via the CLI flag:

```bash
python backup/backup.py --gui
```

Your browser will open automatically (by default at `http://127.0.0.1:8080`). From the interface you can:

1. Verify the live connection badge (`Connected to 10.11.99.1`).
2. Browse, search, and check/uncheck the **folders** or **documents** you want to back up.
3. Choose the export format (**PDF**, **reMarkable Native `.rmdoc`**, or **Both**).
4. Set your target output directory and skip options.
5. Click **Start Backup** and watch real-time progress, file sizes, and activity logs.

Server flags:
- `--port 8080`: Choose the HTTP port (auto-increments if already in use).
- `--no-browser`: Start the server without auto-opening a browser tab.

---

### Option B — Command-Line Interface (CLI)

Back up the entire tablet as PDF (default):

```bash
python backup/backup.py
```

Back up the entire tablet in native `.rmdoc` format (fast, preserves layers):

```bash
python backup/backup.py --format rmdoc
```

Back up only specific folders (by folder path or name) in both formats:

```bash
python backup/backup.py --format both --folder "L3" --folder "Reseau" --output ./my_backups
```

Available CLI flags:

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--gui` | `false` | Launch the Web GUI instead of CLI mode. |
| `--format {pdf,rmdoc,both}` | `pdf` | Export format (`pdf`, `rmdoc`, or `both`). |
| `--output DIR` | `export_remarkable` | Destination directory for exported files. |
| `--folder PATH_OR_ID` | *(all)* | Folder path (e.g. `L3/Algo`) or tablet GUID to export. Can be passed multiple times. |
| `--overwrite` | `false` | Re-download files even if they already exist locally. |
| `--ignore-skipped` | `false` | Ignore `skipped.txt` and retry previously failed files. |
| `--timeout SECONDS` | `90` | Per-file download timeout in seconds. |
| `--port PORT` | `8080` | Port for the Web GUI when `--gui` is used. |

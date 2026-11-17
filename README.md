# reMarkable Tools

A personal collection of lightweight, safe utilities for managing and backing up a **reMarkable** paper tablet.

---

## Available & Planned Tools

| Tool | Status | Description | Documentation |
| :--- | :--- | :--- | :--- |
| **USB Backup (`backup/`)** | Available | Selective or full tablet backup over the official USB Web Interface (`http://10.11.99.1`), preserving your folder hierarchy in `.pdf`, `.rmdoc`, or both, with a modern Web GUI and CLI. | [backup/README.md](backup/README.md) |
| **SSH 50-Day Sync Bypass** | Planned | Upcoming SSH utility to update document modification timestamps on the tablet in order to bypass the 50-day cloud sync restriction without a reMarkable Connect subscription. | *Coming soon* |

---

## Prerequisites (USB Web Interface)

The backup tool relies exclusively on reMarkable's **official local USB Web Interface** (`http://10.11.99.1`), making it completely read-only and safe for your tablet's filesystem:

1. Connect your reMarkable tablet to your computer using a USB cable.
2. On the tablet, go to **Settings &rarr; Storage &rarr; USB web interface** and toggle it **On**.
3. Wait a few seconds for the USB network interface (`10.11.99.1`) to come up.

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/Elouan1411/remarkable-tools.git
cd remarkable-tools

# 2. Create and activate the Python virtual environment
python3 -m venv venvPython

# Bash / Zsh:
source venvPython/bin/activate
# Fish shell:
source venvPython/bin/activate.fish

# 3. Install dependencies
pip install -r backup/requirements.txt

# 4. Launch the Web GUI
python backup/app.py
```

---

## Repository Structure

```text
remarkable-tools/
├── README.md
├── .gitignore
└── backup/
    ├── README.md          # Detailed guide for the USB Backup tool
    ├── requirements.txt   # Python dependencies
    ├── app.py             # Local HTTP server & REST API for the Web GUI
    ├── backup.py          # Core backup engine & CLI
    └── static/            # HTML / CSS / JS frontend
        ├── index.html
        ├── style.css
        └── app.js
```

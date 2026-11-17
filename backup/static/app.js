const state = {
  tree: [],
  connected: false,
  selectedDocIds: new Set(),
  expandedFolderIds: new Set(),
  allDocIds: [],
  allFolderIds: [],
  foldersOnly: false,
  hasLoadedOnce: false,
  searchQuery: "",
  backupState: "idle",
  nextLogIndex: 0,
  pollTimer: null,
};

const els = {
  connectionBadge: document.getElementById("connection-badge"),
  connectionText: document.getElementById("connection-text"),
  refreshBtn: document.getElementById("refresh-btn"),
  selectionSummary: document.getElementById("selection-summary"),
  foldersOnlyToggle: document.getElementById("folders-only-toggle"),
  treeSearch: document.getElementById("tree-search"),
  selectAllBtn: document.getElementById("select-all-btn"),
  deselectAllBtn: document.getElementById("deselect-all-btn"),
  expandAllBtn: document.getElementById("expand-all-btn"),
  collapseAllBtn: document.getElementById("collapse-all-btn"),
  treeContainer: document.getElementById("tree-container"),
  formatCards: document.querySelectorAll(".format-card"),
  outputDirInput: document.getElementById("output-dir-input"),
  skipExistingCheckbox: document.getElementById("skip-existing-checkbox"),
  useSkiplistCheckbox: document.getElementById("use-skiplist-checkbox"),
  skippedCountBadge: document.getElementById("skipped-count-badge"),
  clearSkippedBtn: document.getElementById("clear-skipped-btn"),
  actionError: document.getElementById("action-error"),
  startBackupBtn: document.getElementById("start-backup-btn"),
  cancelBackupBtn: document.getElementById("cancel-backup-btn"),
  jobStatePill: document.getElementById("job-state-pill"),
  progressBarFill: document.getElementById("progress-bar-fill"),
  progressPercent: document.getElementById("progress-percent"),
  progressCounter: document.getElementById("progress-counter"),
  currentFileLabel: document.getElementById("current-file-label"),
  statDownloaded: document.getElementById("stat-downloaded"),
  statSkipped: document.getElementById("stat-skipped"),
  statFailed: document.getElementById("stat-failed"),
  statMb: document.getElementById("stat-mb"),
  clearLogBtn: document.getElementById("clear-log-btn"),
  logConsole: document.getElementById("log-console"),
};

function indexTree(nodes) {
  const docIds = [];
  const folderIds = [];

  function walk(list) {
    for (const node of list) {
      if (node.type === "folder") {
        folderIds.push(node.id);
        node._docIds = collectNodeDocIds(node);
        walk(node.children || []);
      } else if (node.type === "document") {
        docIds.push(node.id);
      }
    }
  }

  walk(nodes);
  state.allDocIds = docIds;
  state.allFolderIds = folderIds;
}

function collectNodeDocIds(node) {
  if (node.type === "document") {
    return [node.id];
  }
  const ids = [];
  for (const child of node.children || []) {
    ids.push(...collectNodeDocIds(child));
  }
  return ids;
}

function getFolderCheckState(folderNode) {
  const childDocs = folderNode._docIds || [];
  if (childDocs.length === 0) {
    return { checked: false, indeterminate: false, selectedCount: 0 };
  }
  let selectedCount = 0;
  for (const id of childDocs) {
    if (state.selectedDocIds.has(id)) {
      selectedCount += 1;
    }
  }
  if (selectedCount === 0) {
    return { checked: false, indeterminate: false, selectedCount: 0 };
  }
  if (selectedCount === childDocs.length) {
    return { checked: true, indeterminate: false, selectedCount };
  }
  return { checked: false, indeterminate: true, selectedCount };
}

function countActiveFolders(nodes) {
  let count = 0;
  for (const node of nodes) {
    if (node.type === "folder") {
      const status = getFolderCheckState(node);
      if (status.checked || status.indeterminate) {
        count += 1;
      }
      count += countActiveFolders(node.children || []);
    }
  }
  return count;
}

function nodeMatchesSearch(node, query) {
  if (!query) return true;
  if ((node.name || "").toLowerCase().includes(query)) return true;
  if (node.type === "folder") {
    return (node.children || []).some((child) => nodeMatchesSearch(child, query));
  }
  return false;
}

function renderTree() {
  if (!state.connected && state.tree.length === 0) {
    return;
  }

  const query = state.searchQuery.trim().toLowerCase();
  const rootUl = buildTreeList(state.tree, query, false);

  els.treeContainer.innerHTML = "";
  if (!rootUl || rootUl.children.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = query
      ? `<p>No folders or documents match "<strong>${escapeHtml(state.searchQuery)}</strong>".</p>`
      : `<p>No documents found on your reMarkable tablet.</p>`;
    els.treeContainer.appendChild(empty);
  } else {
    els.treeContainer.appendChild(rootUl);
  }

  updateSelectionUI();
}

function buildTreeList(nodes, query, isNested) {
  const ul = document.createElement("ul");
  ul.className = isNested ? "tree-list nested" : "tree-list";

  for (const node of nodes) {
    if (state.foldersOnly && node.type === "document") {
      continue;
    }
    if (!nodeMatchesSearch(node, query)) {
      continue;
    }

    const li = document.createElement("li");
    const row = document.createElement("div");
    row.className = `tree-row ${node.type === "folder" ? "is-folder" : "is-doc"}`;

    if (node.type === "folder") {
      const hasVisibleChildren = (node.children || []).some(
        (c) => (!state.foldersOnly || c.type === "folder") && nodeMatchesSearch(c, query)
      );
      const isExpanded = Boolean(query) || state.expandedFolderIds.has(node.id);

      if (hasVisibleChildren) {
        const caret = document.createElement("button");
        caret.type = "button";
        caret.className = "tree-caret";
        caret.setAttribute("aria-label", isExpanded ? "Collapse folder" : "Expand folder");
        caret.textContent = isExpanded ? "▾" : "▸";
        caret.addEventListener("click", () => {
          if (state.expandedFolderIds.has(node.id)) {
            state.expandedFolderIds.delete(node.id);
          } else {
            state.expandedFolderIds.add(node.id);
          }
          renderTree();
        });
        row.appendChild(caret);
      } else {
        const spacer = document.createElement("span");
        spacer.className = "tree-caret-spacer";
        row.appendChild(spacer);
      }

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "tree-checkbox";
      const checkStatus = getFolderCheckState(node);
      checkbox.checked = checkStatus.checked;
      checkbox.indeterminate = checkStatus.indeterminate;
      checkbox.disabled = (node._docIds || []).length === 0;
      checkbox.addEventListener("change", () => {
        toggleFolderSelection(node, checkbox.checked);
      });
      row.appendChild(checkbox);

      const icon = document.createElement("span");
      icon.className = "tree-icon";
      icon.textContent = "📁";
      row.appendChild(icon);

      const label = document.createElement("span");
      label.className = "tree-label";
      label.textContent = node.name;
      label.title = node.rel_path;
      label.addEventListener("click", () => {
        if (hasVisibleChildren) {
          if (state.expandedFolderIds.has(node.id)) {
            state.expandedFolderIds.delete(node.id);
          } else {
            state.expandedFolderIds.add(node.id);
          }
          renderTree();
        } else if (!checkbox.disabled) {
          toggleFolderSelection(node, !checkbox.checked);
        }
      });
      row.appendChild(label);

      const badge = document.createElement("span");
      badge.className = "tree-badge";
      const totalDocs = node.doc_count || 0;
      badge.textContent =
        checkStatus.selectedCount > 0 && checkStatus.selectedCount < totalDocs
          ? `${checkStatus.selectedCount}/${totalDocs} docs`
          : `${totalDocs} ${totalDocs === 1 ? "doc" : "docs"}`;
      row.appendChild(badge);

      li.appendChild(row);

      if (hasVisibleChildren && isExpanded) {
        li.appendChild(buildTreeList(node.children || [], query, true));
      }
    } else {
      const spacer = document.createElement("span");
      spacer.className = "tree-caret-spacer";
      row.appendChild(spacer);

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "tree-checkbox";
      checkbox.checked = state.selectedDocIds.has(node.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.selectedDocIds.add(node.id);
        } else {
          state.selectedDocIds.delete(node.id);
        }
        renderTree();
      });
      row.appendChild(checkbox);

      const icon = document.createElement("span");
      icon.className = "tree-icon";
      icon.textContent = node.file_type === "pdf" ? "📄" : "📝";
      row.appendChild(icon);

      const label = document.createElement("span");
      label.className = "tree-label";
      label.textContent = node.name;
      label.title = node.rel_path;
      label.addEventListener("click", () => {
        if (state.selectedDocIds.has(node.id)) {
          state.selectedDocIds.delete(node.id);
        } else {
          state.selectedDocIds.add(node.id);
        }
        renderTree();
      });
      row.appendChild(label);

      const typeTag = document.createElement("span");
      typeTag.className = "tree-badge type-tag";
      typeTag.textContent = node.file_type || "notebook";
      row.appendChild(typeTag);

      li.appendChild(row);
    }

    ul.appendChild(li);
  }

  return ul;
}

function toggleFolderSelection(folderNode, shouldSelect) {
  const docIds = folderNode._docIds || [];
  for (const id of docIds) {
    if (shouldSelect) {
      state.selectedDocIds.add(id);
    } else {
      state.selectedDocIds.delete(id);
    }
  }
  renderTree();
}

function updateSelectionUI() {
  const selectedDocs = state.selectedDocIds.size;
  const totalDocs = state.allDocIds.length;
  const activeFolders = countActiveFolders(state.tree);

  if (totalDocs === 0 && !state.connected) {
    els.selectionSummary.textContent = "Waiting for tablet connection";
  } else {
    els.selectionSummary.textContent =
      `${activeFolders} folder(s), ${selectedDocs} of ${totalDocs} document(s) selected`;
  }

  const isRunning = state.backupState === "running";
  els.startBackupBtn.textContent = `Start Backup (${selectedDocs} ${
    selectedDocs === 1 ? "document" : "documents"
  })`;
  els.startBackupBtn.disabled = isRunning || !state.connected || selectedDocs === 0;
}

function setConnectionUI(connected) {
  state.connected = connected;
  els.connectionBadge.classList.remove(
    "status-checking",
    "status-connected",
    "status-disconnected"
  );
  if (connected) {
    els.connectionBadge.classList.add("status-connected");
    els.connectionText.textContent = "Connected to 10.11.99.1";
  } else {
    els.connectionBadge.classList.add("status-disconnected");
    els.connectionText.textContent = "Tablet not detected";
  }
  updateSelectionUI();
}

function renderDisconnectedState(message) {
  els.treeContainer.innerHTML = `
    <div class="empty-state">
      <h3>reMarkable Tablet Not Connected</h3>
      <p>${escapeHtml(message)}</p>
      <ol class="setup-steps">
        <li>Connect your reMarkable tablet to this computer with a USB cable.</li>
        <li>On the tablet, open <strong>Settings &rarr; Storage &rarr; USB web interface</strong> and turn it <strong>On</strong>.</li>
        <li>Click <strong>Retry Connection</strong> below.</li>
      </ol>
      <button id="retry-connect-btn" class="btn btn-primary" type="button">Retry Connection</button>
    </div>
  `;
  const retryBtn = document.getElementById("retry-connect-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", () => loadTree(true));
  }
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    setConnectionUI(Boolean(data.connected));
    if (!els.outputDirInput.value && data.default_output_dir) {
      els.outputDirInput.value = data.default_output_dir;
    }
    updateSkippedBadge(data.skipped_count || 0);
    if (data.backup_state === "running") {
      setRunningUI(true);
      startProgressPolling();
    }
  } catch (err) {
    setConnectionUI(false);
  }
}

async function loadTree(refresh = false) {
  els.refreshBtn.disabled = true;
  if (state.tree.length === 0) {
    els.treeContainer.innerHTML = `
      <div class="empty-state">
        <div class="spinner"></div>
        <p>Scanning folders and documents on your reMarkable...</p>
      </div>
    `;
  }

  try {
    const res = await fetch(`/api/tree?refresh=${refresh ? "1" : "0"}`);
    const data = await res.json();
    if (!res.ok) {
      setConnectionUI(false);
      state.tree = [];
      state.allDocIds = [];
      state.allFolderIds = [];
      renderDisconnectedState(data.error || "Unable to reach http://10.11.99.1.");
      return;
    }

    setConnectionUI(true);
    state.tree = data.tree || [];
    indexTree(state.tree);

    // Default to all documents selected on first successful load; preserve selection on refresh
    if (!state.hasLoadedOnce) {
      state.selectedDocIds = new Set(state.allDocIds);
      state.hasLoadedOnce = true;
    } else {
      const validIds = new Set(state.allDocIds);
      state.selectedDocIds = new Set(
        [...state.selectedDocIds].filter((id) => validIds.has(id))
      );
    }

    renderTree();
  } catch (err) {
    setConnectionUI(false);
    renderDisconnectedState("Failed to reach local Python backend.");
  } finally {
    els.refreshBtn.disabled = false;
  }
}

function updateSkippedBadge(count) {
  els.skippedCountBadge.textContent = String(count);
  els.clearSkippedBtn.disabled = count === 0;
}

function getSelectedFormat() {
  const checked = document.querySelector('input[name="export_format"]:checked');
  return checked ? checked.value : "pdf";
}

function setRunningUI(isRunning) {
  state.backupState = isRunning ? "running" : state.backupState;
  els.startBackupBtn.classList.toggle("hidden", isRunning);
  els.cancelBackupBtn.classList.toggle("hidden", !isRunning);
  els.cancelBackupBtn.disabled = false;
  updateSelectionUI();
}

function showError(msg) {
  if (!msg) {
    els.actionError.classList.add("hidden");
    els.actionError.textContent = "";
    return;
  }
  els.actionError.textContent = msg;
  els.actionError.classList.remove("hidden");
}

function appendLogEntries(entries) {
  if (!entries || entries.length === 0) return;
  const fragment = document.createDocumentFragment();
  for (const entry of entries) {
    const line = document.createElement("div");
    line.className = `log-line log-level-${entry.level || "info"}`;
    line.innerHTML = `<span class="log-time">[${escapeHtml(entry.time || "")}]</span>${escapeHtml(entry.message || "")}`;
    fragment.appendChild(line);
  }
  els.logConsole.appendChild(fragment);
  els.logConsole.scrollTop = els.logConsole.scrollHeight;
}

function updateProgressUI(data) {
  state.backupState = data.state || "idle";
  els.jobStatePill.textContent = state.backupState;
  els.jobStatePill.className = `state-pill state-${state.backupState}`;

  const total = data.total || 0;
  const current = data.current || 0;
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;

  els.progressBarFill.style.width = `${pct}%`;
  els.progressPercent.textContent = `${pct}%`;
  els.progressCounter.textContent = `${current} / ${total}`;
  els.currentFileLabel.textContent = data.current_file
    ? `Current: ${data.current_file}`
    : state.backupState === "completed"
    ? `Saved in ${data.output_dir}`
    : "Waiting to start...";

  els.statDownloaded.textContent = String(data.downloaded ?? 0);
  els.statSkipped.textContent = String(data.skipped ?? 0);
  els.statFailed.textContent = String(data.failed ?? 0);
  els.statMb.textContent = Number(data.total_mb ?? 0).toFixed(1);

  if (typeof data.skipped_count === "number") {
    updateSkippedBadge(data.skipped_count);
  }

  if (Array.isArray(data.logs) && data.logs.length > 0) {
    appendLogEntries(data.logs);
  }
  if (typeof data.next_log_index === "number") {
    state.nextLogIndex = data.next_log_index;
  }

  const isRunning = state.backupState === "running";
  setRunningUI(isRunning);
  if (!isRunning && state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startProgressPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
  }
  const poll = async () => {
    try {
      const res = await fetch(`/api/backup/progress?since=${state.nextLogIndex}`);
      const data = await res.json();
      updateProgressUI(data);
    } catch (err) {
      // Ignore transient poll errors
    }
  };
  poll();
  state.pollTimer = setInterval(poll, 450);
}

async function startBackup() {
  showError("");
  const selectedIds = [...state.selectedDocIds];
  if (selectedIds.length === 0) {
    showError("Select at least one folder or document to back up.");
    return;
  }

  els.logConsole.innerHTML = "";
  state.nextLogIndex = 0;

  try {
    const res = await fetch("/api/backup/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_doc_ids: selectedIds,
        export_format: getSelectedFormat(),
        output_dir: els.outputDirInput.value.trim(),
        skip_existing: els.skipExistingCheckbox.checked,
        use_skip_list: els.useSkiplistCheckbox.checked,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      showError(data.error || "Failed to start backup.");
      return;
    }

    setRunningUI(true);
    startProgressPolling();
  } catch (err) {
    showError("Could not reach the local server to start the backup.");
  }
}

async function cancelBackup() {
  els.cancelBackupBtn.disabled = true;
  try {
    await fetch("/api/backup/cancel", { method: "POST" });
  } catch (err) {
    // Polling will catch final state
  }
}

async function clearSkippedList() {
  try {
    const res = await fetch("/api/skipped/clear", { method: "POST" });
    if (res.ok) {
      updateSkippedBadge(0);
      appendLogEntries([
        {
          time: new Date().toTimeString().slice(0, 8),
          level: "info",
          message: "Cleared skipped.txt blacklist.",
        },
      ]);
    }
  } catch (err) {
    showError("Failed to clear skipped.txt.");
  }
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function bindEvents() {
  els.refreshBtn.addEventListener("click", () => loadTree(true));

  els.foldersOnlyToggle.addEventListener("change", () => {
    state.foldersOnly = els.foldersOnlyToggle.checked;
    renderTree();
  });

  els.treeSearch.addEventListener("input", () => {
    state.searchQuery = els.treeSearch.value;
    renderTree();
  });

  els.selectAllBtn.addEventListener("click", () => {
    state.selectedDocIds = new Set(state.allDocIds);
    renderTree();
  });

  els.deselectAllBtn.addEventListener("click", () => {
    state.selectedDocIds.clear();
    renderTree();
  });

  els.expandAllBtn.addEventListener("click", () => {
    state.expandedFolderIds = new Set(state.allFolderIds);
    renderTree();
  });

  els.collapseAllBtn.addEventListener("click", () => {
    state.expandedFolderIds.clear();
    renderTree();
  });

  els.formatCards.forEach((card) => {
    const radio = card.querySelector('input[type="radio"]');
    const syncSelected = () => {
      els.formatCards.forEach((c) => {
        const r = c.querySelector('input[type="radio"]');
        c.classList.toggle("selected", Boolean(r && r.checked));
      });
    };
    card.addEventListener("click", () => {
      if (radio) radio.checked = true;
      syncSelected();
    });
    if (radio) {
      radio.addEventListener("change", syncSelected);
    }
  });

  els.startBackupBtn.addEventListener("click", startBackup);
  els.cancelBackupBtn.addEventListener("click", cancelBackup);
  els.clearSkippedBtn.addEventListener("click", clearSkippedList);
  els.clearLogBtn.addEventListener("click", () => {
    els.logConsole.innerHTML = "";
  });
}

bindEvents();
loadStatus().then(() => loadTree(false));

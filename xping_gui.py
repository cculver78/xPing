"""
xping_gui.py — PyQt6 frontend for xping.py (--json mode)
Requires: PyQt6
   pip install PyQt6

# Copyright (c) 2025 Charles Culver
# [GitHub](https://github.com/cculver78) • [Bluesky](https://bsky.app/profile/dhelmet78.bsky.social) • [Threads](https://www.threads.com/@cculver78)
# Licensed under the MIT License. See LICENSE file for details.
"""

import json, sys
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess, QTimer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QCheckBox,
    QTextEdit, QComboBox, QMessageBox, QGroupBox, QFormLayout, QFileDialog, QLabel
)

SCRIPT_DIR = Path(__file__).resolve().parent
CLI_PATH = SCRIPT_DIR / "xping.py"

VERSION = "1.0.0"

def which_python() -> str:
    # Use the current interpreter to avoid venv surprises
    return sys.executable or "python3"

class XPingGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("xPing GUI")
        self.resize(1000, 640)

        self.proc = None
        self.host_rows = {}  # name -> row index
        self.stopping = False

        self.last_snapshot = []  # cache of last hosts snapshot from CLI
        self.last_seen_len = {}  # host -> last history length we saw (for GUI beeps)

        # --- Controls ---
        # Hosts box (left)
        self.hosts_edit = QTextEdit()
        self.hosts_edit.setPlaceholderText("One host per line (IP or DNS)\nExample:\n192.168.1.1\n1.1.1.1\n8.8.8.8\n\n—or just click START to use default hosts—")
        self.hosts_edit.setMinimumHeight(120)

        hosts_box = QGroupBox("Hosts")
        hosts_layout = QVBoxLayout()
        hosts_layout.addWidget(self.hosts_edit)
        hosts_box.setLayout(hosts_layout)

        # Options box (right)
        self.interval = QSpinBox(); self.interval.setRange(1, 10); self.interval.setValue(1)
        self.losswin  = QSpinBox(); self.losswin.setRange(5, 200); self.losswin.setValue(30)
        self.histsize = QSpinBox(); self.histsize.setRange(10, 400); self.histsize.setValue(40)
        self.timeout  = QSpinBox(); self.timeout.setRange(100, 5000); self.timeout.setSingleStep(100); self.timeout.setValue(1000)

        self.sort = QComboBox(); self.sort.addItems(["name", "rtt", "loss", "jitter", "avg"])
        self.desc = QCheckBox("Descending")

        options_box = QGroupBox("Options")
        form = QFormLayout()
        form.addRow("Interval (s):", self.interval)
        form.addRow("Loss window:", self.losswin)
        form.addRow("History size:", self.histsize)
        form.addRow("Timeout (ms):", self.timeout)
        form.addRow("Sort by:", self.sort)
        form.addRow("", self.desc)
        self.beep = QCheckBox("Beep on Reply")
        form.addRow("", self.beep)
        # Version label at bottom of Options
        ver_row = QHBoxLayout()
        ver_row.addStretch()
        ver_row.addWidget(QLabel(f"Version: {VERSION}"))
        form.addRow(ver_row)
        options_box.setLayout(form)
        options_box.setMinimumWidth(320)

        # react to sort UI changes now that widgets exist
        self.sort.currentIndexChanged.connect(self.resort_current)
        self.desc.stateChanged.connect(self.resort_current)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn  = QPushButton("Stop")
        self.quit_btn  = QPushButton("Quit")
        self.export_btn = QPushButton("Export Results")
        self.export_btn.setVisible(False)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.quit_btn)

        # --- Table ---
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Host", "RTT", "Jitter", "Loss %", "AVG", "History (newest→oldest)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        # --- Layout ---
        layout = QVBoxLayout()
        top_row = QHBoxLayout()
        top_row.addWidget(hosts_box, 2)
        top_row.addWidget(options_box, 1)
        layout.addLayout(top_row)
        layout.addLayout(btn_row)
        layout.addWidget(self.table)
        self.setLayout(layout)

        # --- Signals ---
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.quit_btn.clicked.connect(self.close)
        self.export_btn.clicked.connect(self.export_results)

        # Keep UI responsive even if no output for a bit
        self.idle_timer = QTimer(self); self.idle_timer.setInterval(1000); self.idle_timer.timeout.connect(lambda: None)

    def start(self):
        if not CLI_PATH.exists():
            QMessageBox.critical(self, "Error", f"CLI not found:\n{CLI_PATH}")
            return
        hosts = [h.strip() for h in self.hosts_edit.toPlainText().splitlines() if h.strip()]
        if not hosts:
            # Fallback to CLI defaults
            hosts = []

        # Hide export button for a new session
        self.export_btn.setVisible(False)

        # Build args
        args = [
            str(CLI_PATH),
            "--json",
            "--interval", str(self.interval.value()),
            "--loss-window", str(self.losswin.value()),
            "--hist-size", str(self.histsize.value()),
            "--timeout-ms", str(self.timeout.value()),
        ]
        if self.beep.isChecked():
            args.append("--beep")
        if hosts:
            args.extend(["--hosts"] + hosts)

        # Reset per-host counters for GUI beeps
        self.last_seen_len.clear()
        
        # Reset table
        self.table.setRowCount(0)
        self.host_rows.clear()

        # Spawn process
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.errorOccurred.connect(self.proc_error)
        self.proc.finished.connect(self.proc_finished)

        py = which_python()
        self.proc.start(py, args)
        if not self.proc.waitForStarted(3000):
            QMessageBox.critical(self, "Error", "Failed to start CLI process.")
            self.proc = None
            return

        self.stopping = False
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.idle_timer.start()

    def stop(self):
        if self.proc:
            self.stopping = True
            # Try graceful terminate first
            self.proc.terminate()
            if not self.proc.waitForFinished(1500):
                # Force kill if it doesn't exit
                self.proc.kill()
                self.proc.waitForFinished(1000)
            self.proc = None
        # Reveal export button if we have data to save
        if self.last_snapshot:
            self.export_btn.setVisible(True)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.idle_timer.stop()

    def proc_error(self, err):
        # Suppress crash notifications when we're intentionally stopping
        if self.stopping:
            return
        QMessageBox.critical(self, "Process Error", f"xPing CLI error: {err}")
        self.stop()

    def proc_finished(self):
        self.stopping = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.proc = None
        self.idle_timer.stop()

    def read_output(self):
        if not self.proc:
            return
        while self.proc.canReadLine():
            raw = bytes(self.proc.readLine()).decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            # Expect newline-delimited JSON
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Not JSON: ignore or log
                continue
            if data.get("type") == "snapshot":
                self.last_snapshot = data.get("hosts", [])
                # GUI-side beeping: beep once per new successful sample, per host
                if self.beep.isChecked():
                    for h in self.last_snapshot:
                        name = h.get("name", "")
                        hist = h.get("history", [])
                        cur_len = len(hist)
                        prev_len = self.last_seen_len.get(name, 0)
                        if cur_len > prev_len:
                            # New sample arrived; beep only if it's a successful reply
                            if cur_len > 0 and hist[-1] is not None:
                                QApplication.beep()
                            self.last_seen_len[name] = cur_len
                sorted_hosts = self.sort_snapshot(self.last_snapshot)
                self.update_table(sorted_hosts)

    def resort_current(self):
        if not self.last_snapshot:
            return
        sorted_hosts = self.sort_snapshot(self.last_snapshot)
        self.update_table(sorted_hosts)

    def ensure_row(self, name: str) -> int:
        if name in self.host_rows:
            return self.host_rows[name]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.host_rows[name] = row
        # Pre-create columns
        for c in range(self.table.columnCount()):
            item = QTableWidgetItem("")
            if c == 0:
                item.setData(Qt.ItemDataRole.DisplayRole, name)
            self.table.setItem(row, c, item)
        return row

    def sort_snapshot(self, hosts_snapshot):
        key = self.sort.currentText()
        desc = self.desc.isChecked()

        def k(h):
            # None-safe extraction for numeric sorts
            def val(field):
                v = h.get(field)
                if v is None:
                    return float('inf') if not desc else float('-inf')
                return float(v)
            if key == "name":
                return h.get("name", "").lower()
            if key == "rtt":
                return val("rtt")
            if key == "loss":
                return val("loss_pct")
            if key == "jitter":
                return val("jitter")
            if key == "avg":
                return val("avg")
            return h.get("name", "").lower()

        return sorted(hosts_snapshot, key=k, reverse=desc)

    def move_row(self, old_row, new_row):
        if old_row == new_row:
            return
        cols = self.table.columnCount()
        items = [self.table.takeItem(old_row, c) for c in range(cols)]
        self.table.removeRow(old_row)
        # adjust target index if removing from above
        if old_row < new_row:
            new_row -= 1
        self.table.insertRow(new_row)
        for c, it in enumerate(items):
            if it is None:
                it = QTableWidgetItem("")
            self.table.setItem(new_row, c, it)

    def rebuild_row_index(self):
        self.host_rows.clear()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            name = it.text() if it else ""
            self.host_rows[name] = r

    def update_table(self, hosts_snapshot):
        # First pass: ensure rows exist and update cell contents
        for h in hosts_snapshot:
            name = h.get("name", "?")
            row = self.ensure_row(name)

            rtt = h.get("rtt")
            jitter = h.get("jitter")
            loss = h.get("loss_pct")
            avg = h.get("avg")
            hist = h.get("history", [])

            # Build left-anchored history string (newest on left)
            tail = hist[-24:]
            disp = list(reversed(tail))  # newest first
            disp += [None] * (24 - len(disp))
            tokens = [("---" if v is None else f"{int(v):>3}") for v in disp]
            hist_str = " ".join(tokens)

            def set_col(col, val):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem("")
                    self.table.setItem(row, col, item)
                item.setText(val)

            set_col(0, name)
            set_col(1, "--" if rtt is None else str(int(rtt)))
            set_col(2, "--" if jitter is None else str(int(jitter)))
            set_col(3, "--" if loss is None else str(int(loss)))
            set_col(4, "--" if avg is None else str(int(avg)))
            set_col(5, hist_str)

        # Second pass: physically reorder rows to match hosts_snapshot order
        # Build current mapping name -> row
        self.rebuild_row_index()
        for desired_index, h in enumerate(hosts_snapshot):
            name = h.get("name", "?")
            current_index = self.host_rows.get(name, desired_index)
            if current_index != desired_index:
                self.move_row(current_index, desired_index)
                # After moving, rebuild index to keep it accurate
                self.rebuild_row_index()

        # Resize numeric columns (host column stretches)
        for c in range(1, 6):
            self.table.resizeColumnToContents(c)

    def export_results(self):
        if not self.last_snapshot:
            QMessageBox.information(self, "No Data", "No results available to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Results As", "xping_results.txt", "Text Files (*.txt)"
        )
        if not path:
            return

        lines = ["xPing Results", "=" * 20, ""]
        lines.append(f"{'Host':<24}{'RTT':>8}{'Jitter':>10}{'Loss%':>10}{'AVG':>10}")
        lines.append("-" * 62)
        for h in self.sort_snapshot(self.last_snapshot):
            host = h.get("name", "?")
            rtt  = h.get("rtt")
            jit  = h.get("jitter")
            loss = h.get("loss_pct")
            avg  = h.get("avg")
            lines.append(f"{host:<24}{('--' if rtt is None else int(rtt)):>8}{('--' if jit is None else int(jit)):>10}{('--' if loss is None else int(loss)):>10}{('--' if avg is None else int(avg)):>10}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            QMessageBox.information(self, "Export Complete", f"Results saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    def closeEvent(self, event):
        self.stopping = True
        self.stop()
        return super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    gui = XPingGUI()
    gui.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
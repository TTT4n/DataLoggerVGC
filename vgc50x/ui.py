import ctypes
import queue
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, ttk

import serial

from vgc50x.config import (
    APP_AUTHOR,
    APP_VERSION,
    BAUDRATE_CANDIDATES,
    COMMAND_CHOICES,
    DEFAULT_BAUDRATE,
    DEFAULT_CHANNEL_COMMAND,
    DEFAULT_COM_PORT,
    DEFAULT_CSV_FILE,
    DEFAULT_INTERVAL_SEC,
    DEFAULT_PREVENT_SLEEP,
    DEFAULT_ROTATE_HOURS,
    MAX_LOG_LINES,
    MAX_PLOT_POINTS,
)
from vgc50x.session import LoggerSession
from vgc50x.protocol import list_serial_port_names

# ── Palette ──────────────────────────────────────────────────────────────────
_BG         = "#f1f5f9"
_SIDEBAR_BG = "#ffffff"
_TOP_BG     = "#1e293b"
_CARD_BG    = "#ffffff"
_BORDER     = "#e2e8f0"
_ACCENT     = "#0d9488"
_ACCENT_ACT = "#0f766e"
_DANGER     = "#ef4444"
_TEXT       = "#0f172a"
_TEXT2      = "#64748b"
_CHART_BG   = "#f8fafc"
_CHART_LINE = "#0d9488"
_CHART_FILL = "#ccfbf1"
_DOT_IDLE   = "#475569"
_DOT_OK     = "#0d9488"
_DOT_WARN   = "#f59e0b"
_DOT_ERR    = "#ef4444"


def _bordered(parent, **kw):
    """tk.Frame with a 1-px border via highlightbackground."""
    return tk.Frame(
        parent,
        bg=kw.pop("bg", _CARD_BG),
        highlightbackground=kw.pop("highlightbackground", _BORDER),
        highlightthickness=1,
        **kw,
    )


class VGC50xLoggerApp:
    ES_CONTINUOUS       = 0x80000000
    ES_SYSTEM_REQUIRED  = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self, root):
        self.root = root
        self.root.title("VGC50x Data Logger")
        self.root.geometry("1240x800")
        self.root.minsize(980, 660)
        self.root.configure(bg=_TOP_BG)

        self.port_var          = tk.StringVar(value=DEFAULT_COM_PORT)
        self.baudrate_var      = tk.StringVar(value=str(DEFAULT_BAUDRATE))
        self.command_var       = tk.StringVar(value=DEFAULT_CHANNEL_COMMAND)
        self.interval_var      = tk.StringVar(value=str(DEFAULT_INTERVAL_SEC))
        self.rotate_hours_var  = tk.StringVar(value=str(DEFAULT_ROTATE_HOURS))
        self.csv_file_var      = tk.StringVar(value=str(DEFAULT_CSV_FILE.resolve()))
        self.prevent_sleep_var = tk.BooleanVar(value=DEFAULT_PREVENT_SLEEP)

        self.connection_var      = tk.StringVar(value="Idle")
        self.last_update_var     = tk.StringVar(value="—")
        self.status_var          = tk.StringVar(value="—")
        self.status_meaning_var  = tk.StringVar(value="—")
        self.pressure_var        = tk.StringVar(value="—")

        self.worker_thread  = None
        self.stop_event     = threading.Event()
        self.message_queue  = queue.Queue()
        self.samples        = deque(maxlen=MAX_PLOT_POINTS)
        self.logging_active = False

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self._configure_styles()
        self._build_ui()
        self.refresh_ports()
        self.apply_sleep_prevention()
        self.root.after(150, self.process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── Style configuration ───────────────────────────────────────────────────

    def _configure_styles(self):
        self.style.configure("TCombobox", fieldbackground=_CARD_BG, background=_CARD_BG)
        self.style.configure("TEntry", fieldbackground=_CARD_BG)
        self.style.configure(
            "Treeview",
            background=_CARD_BG,
            fieldbackground=_CARD_BG,
            foreground=_TEXT,
            rowheight=26,
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Treeview.Heading",
            background=_BG,
            foreground=_TEXT2,
            font=("Segoe UI Semibold", 9),
            relief="flat",
        )
        self.style.map("Treeview",
            background=[("selected", _ACCENT)],
            foreground=[("selected", "#ffffff")],
        )
        self.style.configure(
            "TCheckbutton",
            background=_SIDEBAR_BG,
            foreground=_TEXT2,
            font=("Segoe UI", 9),
        )
        self.style.map("TCheckbutton", background=[("active", _SIDEBAR_BG)])
        self.style.configure("TSeparator", background=_BORDER)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()

        body = tk.Frame(self.root, bg=_BG)
        body.pack(fill="both", expand=True)

        self._build_sidebar(body)
        tk.Frame(body, bg=_BORDER, width=1).pack(side="left", fill="y")
        self._build_main(body)

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=_TOP_BG, height=52)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        tk.Label(bar, text="VGC50x", bg=_TOP_BG, fg="#ffffff",
                 font=("Segoe UI Semibold", 14)).pack(side="left", padx=(20, 4), pady=14)
        tk.Label(bar, text="Data Logger", bg=_TOP_BG, fg="#94a3b8",
                 font=("Segoe UI", 14)).pack(side="left")

        tk.Label(bar, text=APP_VERSION, bg=_TOP_BG, fg="#475569",
                 font=("Segoe UI", 9)).pack(side="right", padx=(0, 20))

        self._dot_canvas = tk.Canvas(bar, width=10, height=10, bg=_TOP_BG, highlightthickness=0)
        self._dot_canvas.pack(side="right", padx=(0, 8))
        self._dot = self._dot_canvas.create_oval(1, 1, 9, 9, fill=_DOT_IDLE, outline="")

        tk.Label(bar, textvariable=self.connection_var, bg=_TOP_BG, fg="#94a3b8",
                 font=("Segoe UI", 9)).pack(side="right", padx=(0, 6))

    def _build_sidebar(self, parent):
        sidebar = tk.Frame(parent, bg=_SIDEBAR_BG, width=248)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        sb = tk.Frame(sidebar, bg=_SIDEBAR_BG, padx=18)
        sb.pack(fill="both", expand=True, pady=18)

        def section(title):
            tk.Frame(sb, bg=_SIDEBAR_BG, height=6).pack()
            tk.Label(sb, text=title, bg=_SIDEBAR_BG, fg=_TEXT,
                     font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0, 6))

        def field(label, widget_factory):
            tk.Label(sb, text=label.upper(), bg=_SIDEBAR_BG, fg=_TEXT2,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(8, 2))
            w = widget_factory(sb)
            w.pack(fill="x")
            return w

        def divider():
            tk.Frame(sb, bg=_BORDER, height=1).pack(fill="x", pady=(14, 0))

        # ── Connection ────────────────────────────────────────────
        section("Connection")

        self.port_combo = field("Port", lambda p: ttk.Combobox(
            p, textvariable=self.port_var, state="readonly"))

        self.baud_combo = field("Baudrate", lambda p: ttk.Combobox(
            p, textvariable=self.baudrate_var, state="readonly",
            values=[str(r) for r in BAUDRATE_CANDIDATES]))

        self.command_combo = field("Channel", lambda p: ttk.Combobox(
            p, textvariable=self.command_var, state="readonly",
            values=COMMAND_CHOICES))

        divider()

        # ── Logging ───────────────────────────────────────────────
        section("Logging")

        self.interval_entry = field("Interval (seconds)", lambda p: ttk.Entry(
            p, textvariable=self.interval_var))

        self.rotate_entry = field("New CSV every (hours)", lambda p: ttk.Entry(
            p, textvariable=self.rotate_hours_var))

        tk.Label(sb, text="CSV FILE", bg=_SIDEBAR_BG, fg=_TEXT2,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(8, 2))
        csv_row = tk.Frame(sb, bg=_SIDEBAR_BG)
        csv_row.pack(fill="x")
        self.csv_entry = ttk.Entry(csv_row, textvariable=self.csv_file_var)
        self.csv_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(csv_row, text="…", width=3, command=self.choose_csv_file)
        self.browse_button.pack(side="left", padx=(4, 0))

        tk.Frame(sb, bg=_SIDEBAR_BG, height=8).pack()
        self.prevent_sleep_check = ttk.Checkbutton(
            sb, text="Keep PC awake",
            variable=self.prevent_sleep_var,
            command=self.apply_sleep_prevention,
        )
        self.prevent_sleep_check.pack(anchor="w")

        # ── Push buttons to bottom ────────────────────────────────
        tk.Frame(sb, bg=_SIDEBAR_BG).pack(fill="both", expand=True)

        divider()
        tk.Frame(sb, bg=_SIDEBAR_BG, height=12).pack()

        ttk.Button(sb, text="Refresh Ports", command=self.refresh_ports).pack(fill="x", pady=(0, 6))

        self.start_button = tk.Button(
            sb, text="▶   Start Logging",
            command=self.start_logging,
            bg=_ACCENT, fg="#ffffff",
            activebackground=_ACCENT_ACT, activeforeground="#ffffff",
            font=("Segoe UI Semibold", 10), relief="flat", bd=0,
            pady=9, cursor="hand2",
        )
        self.start_button.pack(fill="x", pady=(0, 4))

        self.stop_button = tk.Button(
            sb, text="■   Stop",
            command=self.stop_logging,
            bg="#f1f5f9", fg=_TEXT2,
            activebackground="#e2e8f0", activeforeground=_TEXT2,
            font=("Segoe UI Semibold", 10), relief="flat", bd=0,
            pady=9, cursor="hand2",
            state="disabled",
        )
        self.stop_button.pack(fill="x")

        tk.Label(sb, text=APP_AUTHOR, bg=_SIDEBAR_BG, fg=_TEXT2,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(14, 0))

    def _build_main(self, parent):
        main = tk.Frame(parent, bg=_BG)
        main.pack(side="left", fill="both", expand=True)

        # ── Metrics row ───────────────────────────────────────────
        metrics_row = tk.Frame(main, bg=_BG)
        metrics_row.pack(fill="x", padx=18, pady=(18, 0))
        metrics_row.columnconfigure((0, 1, 2), weight=1)

        pressure_card = _bordered(metrics_row, bd=0)
        pressure_card.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tk.Label(pressure_card, text="PRESSURE (mbar)", bg=_CARD_BG, fg=_TEXT2,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(pressure_card, textvariable=self.pressure_var, bg=_CARD_BG, fg=_TEXT,
                 font=("Consolas", 26, "bold")).pack(anchor="w", padx=16, pady=(4, 14))

        status_card = _bordered(metrics_row, bd=0)
        status_card.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        tk.Label(status_card, text="STATUS CODE", bg=_CARD_BG, fg=_TEXT2,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(status_card, textvariable=self.status_var, bg=_CARD_BG, fg=_TEXT,
                 font=("Consolas", 26, "bold")).pack(anchor="w", padx=16, pady=(4, 14))

        meaning_card = _bordered(metrics_row, bd=0)
        meaning_card.grid(row=0, column=2, sticky="ew")
        tk.Label(meaning_card, text="MEANING", bg=_CARD_BG, fg=_TEXT2,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(meaning_card, textvariable=self.status_meaning_var, bg=_CARD_BG, fg=_TEXT,
                 font=("Segoe UI Semibold", 18)).pack(anchor="w", padx=16, pady=(4, 14))

        # ── Chart ─────────────────────────────────────────────────
        chart_outer = tk.Frame(main, bg=_BG)
        chart_outer.pack(fill="both", expand=True, padx=18, pady=(14, 0))

        chart_card = _bordered(chart_outer, bd=0)
        chart_card.pack(fill="both", expand=True)

        chart_hdr = tk.Frame(chart_card, bg=_CARD_BG)
        chart_hdr.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(chart_hdr, text="Live Trend", bg=_CARD_BG, fg=_TEXT,
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        tk.Label(chart_hdr, textvariable=self.last_update_var, bg=_CARD_BG, fg=_TEXT2,
                 font=("Segoe UI", 9)).pack(side="right")

        self.chart_canvas = tk.Canvas(chart_card, bg=_CHART_BG, highlightthickness=0)
        self.chart_canvas.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        self.chart_canvas.bind("<Configure>", lambda _: self.redraw_chart())

        # ── Table ─────────────────────────────────────────────────
        table_outer = tk.Frame(main, bg=_BG)
        table_outer.pack(fill="both", expand=False, padx=18, pady=(12, 18))

        table_card = _bordered(table_outer, bd=0)
        table_card.pack(fill="both", expand=True)

        table_hdr = tk.Frame(table_card, bg=_CARD_BG)
        table_hdr.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(table_hdr, text="Recent Samples", bg=_CARD_BG, fg=_TEXT,
                 font=("Segoe UI Semibold", 11)).pack(side="left")

        tbl_frame = tk.Frame(table_card, bg=_CARD_BG)
        tbl_frame.pack(fill="both", expand=True, padx=16, pady=(8, 14))

        columns = ("timestamp", "command", "status", "meaning", "pressure", "raw")
        self.table = ttk.Treeview(tbl_frame, columns=columns, show="headings", height=7)
        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vsb.set)
        self.table.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.table.heading("timestamp", text="Timestamp")
        self.table.heading("command",   text="Channel")
        self.table.heading("status",    text="Status")
        self.table.heading("meaning",   text="Meaning")
        self.table.heading("pressure",  text="Pressure")
        self.table.heading("raw",       text="Raw Response")

        self.table.column("timestamp", width=155, anchor="w",      stretch=False)
        self.table.column("command",   width=70,  anchor="center", stretch=False)
        self.table.column("status",    width=65,  anchor="center", stretch=False)
        self.table.column("meaning",   width=130, anchor="w",      stretch=False)
        self.table.column("pressure",  width=115, anchor="e",      stretch=False)
        self.table.column("raw",       width=200, anchor="w")

        self.table.tag_configure("odd",  background=_CARD_BG)
        self.table.tag_configure("even", background=_BG)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_dot(self, color):
        self._dot_canvas.itemconfig(self._dot, fill=color)

    def refresh_ports(self):
        ports = list_serial_port_names()
        self.port_combo["values"] = ports

        if not ports:
            self.connection_var.set("No COM ports detected")
            return

        if self.port_var.get() not in ports:
            self.port_var.set(ports[0])

        self.connection_var.set(f"Ports found: {', '.join(ports)}")

    def choose_csv_file(self):
        initial_path = Path(self.csv_file_var.get()).resolve()
        selected = filedialog.asksaveasfilename(
            title="Select CSV log file",
            defaultextension=".csv",
            initialfile=initial_path.name,
            initialdir=str(initial_path.parent),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.csv_file_var.set(selected)

    def set_settings_enabled(self, enabled):
        combo_state  = "readonly" if enabled else "disabled"
        widget_state = "normal"   if enabled else "disabled"

        self.port_combo.configure(state=combo_state)
        self.baud_combo.configure(state=combo_state)
        self.command_combo.configure(state=combo_state)
        self.interval_entry.configure(state=widget_state)
        self.rotate_entry.configure(state=widget_state)
        self.csv_entry.configure(state=widget_state)
        self.browse_button.configure(state=widget_state)
        self.prevent_sleep_check.configure(state=widget_state)

        if enabled:
            self.start_button.configure(state="normal",   bg=_ACCENT,   fg="#ffffff")
            self.stop_button.configure( state="disabled", bg="#f1f5f9",  fg=_TEXT2)
        else:
            self.start_button.configure(state="disabled", bg="#94a3b8",  fg="#ffffff")
            self.stop_button.configure( state="normal",   bg="#fee2e2",  fg=_DANGER,
                                        activebackground="#fecaca", activeforeground=_DANGER)

    def apply_sleep_prevention(self):
        if not hasattr(ctypes, "windll"):
            return
        flags = self.ES_CONTINUOUS
        if self.prevent_sleep_var.get():
            flags |= self.ES_SYSTEM_REQUIRED | self.ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)

    # ── Logging control ───────────────────────────────────────────────────────

    def start_logging(self):
        if self.logging_active:
            return

        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError("Interval must be greater than 0.")
        except ValueError as ex:
            self.connection_var.set(f"Invalid interval: {ex}")
            return

        try:
            rotate_hours = float(self.rotate_hours_var.get())
            if rotate_hours <= 0:
                raise ValueError("Rotate hours must be greater than 0.")
        except ValueError as ex:
            self.connection_var.set(f"Invalid rotate setting: {ex}")
            return

        try:
            preferred_baudrate = int(self.baudrate_var.get())
        except ValueError:
            self.connection_var.set("Invalid baudrate selected.")
            return

        port     = self.port_var.get().strip()
        command  = self.command_var.get().strip().upper()
        csv_file = Path(self.csv_file_var.get()).expanduser()

        if not port:
            self.connection_var.set("Choose a COM port before starting.")
            return

        if not csv_file.parent.exists():
            self.connection_var.set(f"CSV directory does not exist: {csv_file.parent}")
            return

        self.stop_event.clear()
        self.logging_active = True
        self.set_settings_enabled(False)
        self._set_dot(_DOT_WARN)
        self.connection_var.set(f"Connecting to {port}…")

        args = (port, preferred_baudrate, command, interval, rotate_hours, csv_file)
        self.worker_thread = threading.Thread(target=self.logging_worker, args=args, daemon=True)
        self.worker_thread.start()

    def stop_logging(self):
        if not self.logging_active:
            return
        self.stop_event.set()
        self.connection_var.set("Stopping logger…")
        self.stop_button.configure(state="disabled")

    def logging_worker(self, port, preferred_baudrate, command, interval, rotate_hours, csv_file):
        session = LoggerSession(port, preferred_baudrate, command, csv_file, rotate_hours=rotate_hours)
        try:
            details = session.open()
            self.message_queue.put({"type": "connected", **details})

            while not self.stop_event.is_set():
                cycle_start = time.monotonic()
                try:
                    sample = session.read_sample()
                    self.message_queue.put({"type": "sample", **sample})
                except serial.SerialException as ex:
                    raise RuntimeError(f"Serial port lost: {ex}") from ex
                except Exception as ex:
                    self.message_queue.put({"type": "error", "message": str(ex)})
                    if self.stop_event.wait(2.0):
                        break
                    continue

                elapsed   = time.monotonic() - cycle_start
                remaining = max(0.0, interval - elapsed)
                if self.stop_event.wait(remaining):
                    break
        except Exception as ex:
            self.message_queue.put({"type": "fatal", "message": str(ex)})
        finally:
            session.close()
            self.message_queue.put({"type": "stopped"})

    # ── Queue processing ──────────────────────────────────────────────────────

    def process_queue(self):
        while True:
            try:
                msg = self.message_queue.get_nowait()
            except queue.Empty:
                break

            t = msg["type"]

            if t == "connected":
                self.csv_file_var.set(msg["csv_path"])
                self._set_dot(_DOT_OK)
                self.connection_var.set(
                    f"Connected · {msg['port']} · {msg['baudrate']} baud · {Path(msg['csv_path']).name}"
                )
            elif t == "sample":
                self.handle_sample(msg)
            elif t == "error":
                self._set_dot(_DOT_WARN)
                self.connection_var.set(f"Read error: {msg['message']}")
            elif t == "fatal":
                self._set_dot(_DOT_ERR)
                self.connection_var.set(f"Connection failed: {msg['message']}")
            elif t == "stopped":
                self.logging_active = False
                self.set_settings_enabled(True)
                self._set_dot(_DOT_IDLE)
                if not self.connection_var.get().startswith("Connection failed"):
                    self.connection_var.set("Logger stopped")

        self.root.after(150, self.process_queue)

    # ── Sample handling ───────────────────────────────────────────────────────

    def handle_sample(self, sample):
        pressure = sample["pressure"]
        pressure_display = "—" if pressure is None else f"{pressure:.6g}"
        rotation = sample.get("rotated")

        self.pressure_var.set(pressure_display)
        self.status_var.set(sample["status"])
        self.status_meaning_var.set(sample["meaning"])
        self.last_update_var.set(sample["timestamp"])

        if rotation:
            self.csv_file_var.set(rotation["csv_path"])
            self.connection_var.set(f"Log rotated · {Path(rotation['csv_path']).name}")

        row_count = len(self.table.get_children())
        tag = "even" if row_count % 2 == 0 else "odd"
        self.table.insert(
            "", 0,
            values=(
                sample["timestamp"],
                sample["command"],
                sample["status"],
                sample["meaning"],
                pressure_display,
                sample["raw"],
            ),
            tags=(tag,),
        )

        rows = self.table.get_children()
        if len(rows) > MAX_LOG_LINES:
            self.table.delete(*rows[MAX_LOG_LINES:])

        if pressure is not None:
            self.samples.append((sample["timestamp"], pressure))
            self.redraw_chart()

    # ── Chart ─────────────────────────────────────────────────────────────────

    def redraw_chart(self):
        canvas = self.chart_canvas
        canvas.delete("all")

        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 20 or h < 20:
            return

        pl, pr, pt, pb = 56, 16, 14, 30
        pw = w - pl - pr
        ph = h - pt - pb

        if len(self.samples) < 2:
            canvas.create_text(
                w / 2, h / 2,
                text="Start logging to see the live pressure trend",
                fill=_TEXT2, font=("Segoe UI", 11),
            )
            return

        values   = [v for _, v in self.samples]
        min_val  = min(values)
        max_val  = max(values)
        if min_val == max_val:
            min_val -= 1
            max_val += 1
        val_span = max_val - min_val

        # Grid lines + Y labels
        for step in range(5):
            y     = pt + ph * step / 4
            val   = max_val - val_span * step / 4
            canvas.create_line(pl, y, w - pr, y, fill="#e2e8f0", dash=(4, 4))
            canvas.create_text(pl - 6, y, text=f"{val:.3g}", fill=_TEXT2,
                               font=("Segoe UI", 8), anchor="e")

        # Build point coords
        span   = len(values) - 1
        pts    = []
        for i, v in enumerate(values):
            x = pl + pw * i / span
            y = pt + ph - ph * (v - min_val) / val_span
            pts.extend([x, y])

        # Fill polygon (line + baseline)
        fill_poly = list(pts) + [pts[-2], pt + ph, pl, pt + ph]
        canvas.create_polygon(fill_poly, fill=_CHART_FILL, outline="", smooth=True)

        # Line
        canvas.create_line(pts, fill=_CHART_LINE, width=2, smooth=True)

        # Latest-point dot
        lx, ly = pts[-2], pts[-1]
        canvas.create_oval(lx - 5, ly - 5, lx + 5, ly + 5,
                           fill=_CHART_LINE, outline=_CARD_BG, width=2)

        # X-axis time labels
        first = self.samples[0][0][-8:]
        last  = self.samples[-1][0][-8:]
        canvas.create_text(pl,     h - 10, text=first, fill=_TEXT2,
                           font=("Segoe UI", 8), anchor="w")
        canvas.create_text(w - pr, h - 10, text=last,  fill=_TEXT2,
                           font=("Segoe UI", 8), anchor="e")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def on_close(self):
        self.stop_event.set()
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)
        if hasattr(ctypes, "windll"):
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
        self.root.destroy()


def run_app():
    root = tk.Tk()
    VGC50xLoggerApp(root)
    root.mainloop()

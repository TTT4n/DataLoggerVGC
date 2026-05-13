import ctypes
import queue
import threading
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, ttk

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


class VGC50xLoggerApp:
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self, root):
        self.root = root
        self.root.title("VGC50x Data Logger")
        self.root.geometry("1180x760")
        self.root.minsize(980, 680)
        self.root.configure(bg="#eef3f7")

        self.port_var = tk.StringVar(value=DEFAULT_COM_PORT)
        self.baudrate_var = tk.StringVar(value=str(DEFAULT_BAUDRATE))
        self.command_var = tk.StringVar(value=DEFAULT_CHANNEL_COMMAND)
        self.interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_SEC))
        self.rotate_hours_var = tk.StringVar(value=str(DEFAULT_ROTATE_HOURS))
        self.csv_file_var = tk.StringVar(value=str(DEFAULT_CSV_FILE.resolve()))
        self.prevent_sleep_var = tk.BooleanVar(value=DEFAULT_PREVENT_SLEEP)

        self.connection_var = tk.StringVar(value="Idle")
        self.last_update_var = tk.StringVar(value="-")
        self.status_var = tk.StringVar(value="-")
        self.status_meaning_var = tk.StringVar(value="-")
        self.pressure_var = tk.StringVar(value="-")

        self.worker_thread = None
        self.stop_event = threading.Event()
        self.message_queue = queue.Queue()
        self.samples = deque(maxlen=MAX_PLOT_POINTS)
        self.logging_active = False

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self._configure_styles()
        self._build_ui()
        self.refresh_ports()
        self.apply_sleep_prevention()
        self.root.after(150, self.process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_styles(self):
        self.style.configure("Root.TFrame", background="#eef3f7")
        self.style.configure("Card.TFrame", background="#ffffff")
        self.style.configure("Title.TLabel", background="#eef3f7", foreground="#17324d", font=("Segoe UI Semibold", 20))
        self.style.configure("Muted.TLabel", background="#eef3f7", foreground="#5f7488", font=("Segoe UI", 10))
        self.style.configure("CardTitle.TLabel", background="#ffffff", foreground="#17324d", font=("Segoe UI Semibold", 11))
        self.style.configure("CardValue.TLabel", background="#ffffff", foreground="#12263a", font=("Consolas", 18, "bold"))
        self.style.configure("Field.TLabel", background="#ffffff", foreground="#41576c", font=("Segoe UI", 10))
        self.style.configure("Primary.TButton", font=("Segoe UI Semibold", 10))

    def _build_ui(self):
        outer = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer, style="Root.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(header, text="VGC50x Data Logger", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Live pressure monitoring, CSV logging, and recent sample history.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            header,
            text=f"{APP_AUTHOR} | Version {APP_VERSION}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        middle = ttk.Frame(outer, style="Root.TFrame")
        middle.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=2)
        middle.rowconfigure(0, weight=1)

        chart_card = ttk.Frame(middle, style="Card.TFrame", padding=12)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        chart_card.columnconfigure(0, weight=1)
        chart_card.rowconfigure(1, weight=1)
        ttk.Label(chart_card, text="Live Trend", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.chart_canvas = tk.Canvas(chart_card, bg="#f8fbfd", highlightthickness=0, height=360)
        self.chart_canvas.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.chart_canvas.bind("<Configure>", lambda _event: self.redraw_chart())

        controls = ttk.Frame(middle, style="Card.TFrame", padding=14)
        controls.grid(row=0, column=1, sticky="nsew")
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)
        controls.rowconfigure(6, weight=1)

        ttk.Label(controls, text="Connection", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        actions = ttk.Frame(controls, style="Card.TFrame")
        actions.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        actions.columnconfigure(3, weight=1)

        ttk.Button(actions, text="Refresh Ports", command=self.refresh_ports).grid(row=0, column=0, sticky="w")
        self.start_button = ttk.Button(actions, text="Start Logging", command=self.start_logging, style="Primary.TButton")
        self.start_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop_logging, state="disabled")
        self.stop_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(actions, textvariable=self.connection_var, style="Muted.TLabel", anchor="e").grid(
            row=0, column=3, sticky="ew", padx=(12, 0)
        )

        ttk.Label(controls, text="COM Port", style="Field.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        self.port_combo = ttk.Combobox(controls, textvariable=self.port_var, state="readonly", width=18)
        self.port_combo.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(controls, text="Baudrate", style="Field.TLabel").grid(row=2, column=2, sticky="w", padx=(16, 8), pady=4)
        self.baud_combo = ttk.Combobox(
            controls,
            textvariable=self.baudrate_var,
            state="readonly",
            values=[str(rate) for rate in BAUDRATE_CANDIDATES],
            width=12,
        )
        self.baud_combo.grid(row=2, column=3, sticky="ew", pady=4)

        ttk.Label(controls, text="Command", style="Field.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        self.command_combo = ttk.Combobox(
            controls,
            textvariable=self.command_var,
            state="readonly",
            values=COMMAND_CHOICES,
            width=10,
        )
        self.command_combo.grid(row=3, column=1, sticky="ew", pady=4)

        ttk.Label(controls, text="Record every (seconds)", style="Field.TLabel").grid(row=3, column=2, sticky="w", padx=(16, 8), pady=4)
        self.interval_entry = ttk.Entry(controls, textvariable=self.interval_var)
        self.interval_entry.grid(row=3, column=3, sticky="ew", pady=4)

        ttk.Label(controls, text="New CSV every (hours)", style="Field.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        self.rotate_entry = ttk.Entry(controls, textvariable=self.rotate_hours_var)
        self.rotate_entry.grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Label(
            controls,
            text="Example: 2 = create a new CSV every 2 hours while logging",
            style="Field.TLabel",
        ).grid(row=4, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=4)

        ttk.Label(controls, text="CSV File", style="Field.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=4)
        self.csv_entry = ttk.Entry(controls, textvariable=self.csv_file_var)
        self.csv_entry.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4)

        self.browse_button = ttk.Button(controls, text="Browse", command=self.choose_csv_file)
        self.browse_button.grid(row=5, column=3, sticky="ew", pady=4)

        self.prevent_sleep_check = ttk.Checkbutton(
            controls,
            text="Keep notebook awake while app is open",
            variable=self.prevent_sleep_var,
            command=self.apply_sleep_prevention,
        )
        self.prevent_sleep_check.grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))

        status_panel = ttk.Frame(controls, style="Card.TFrame")
        status_panel.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        status_panel.columnconfigure((0, 1), weight=1)
        self._metric_card(status_panel, "Pressure", self.pressure_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._metric_card(status_panel, "Status", self.status_var).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._metric_card(status_panel, "Meaning", self.status_meaning_var).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        self._metric_card(status_panel, "Last Update", self.last_update_var).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))

        log_card = ttk.Frame(outer, style="Card.TFrame", padding=12)
        log_card.grid(row=2, column=0, sticky="nsew")
        ttk.Label(log_card, text="Recent Samples", style="CardTitle.TLabel").pack(anchor="w")

        columns = ("timestamp", "command", "status", "meaning", "pressure", "raw")
        self.table = ttk.Treeview(log_card, columns=columns, show="headings", height=14)
        self.table.pack(fill="both", expand=True, pady=(10, 0))
        self.table.heading("timestamp", text="Timestamp")
        self.table.heading("command", text="Command")
        self.table.heading("status", text="Status")
        self.table.heading("meaning", text="Meaning")
        self.table.heading("pressure", text="Pressure")
        self.table.heading("raw", text="Raw")
        self.table.column("timestamp", width=160, anchor="w")
        self.table.column("command", width=80, anchor="center")
        self.table.column("status", width=70, anchor="center")
        self.table.column("meaning", width=140, anchor="w")
        self.table.column("pressure", width=100, anchor="e")
        self.table.column("raw", width=220, anchor="w")

    def _metric_card(self, parent, title, variable):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=12)
        ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=variable, style="CardValue.TLabel").pack(anchor="w", pady=(8, 0))
        return frame

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
        combo_state = "readonly" if enabled else "disabled"
        widget_state = "normal" if enabled else "disabled"

        self.port_combo.configure(state=combo_state)
        self.baud_combo.configure(state=combo_state)
        self.command_combo.configure(state=combo_state)
        self.interval_entry.configure(state=widget_state)
        self.rotate_entry.configure(state=widget_state)
        self.csv_entry.configure(state=widget_state)
        self.browse_button.configure(state=widget_state)
        self.prevent_sleep_check.configure(state=widget_state)

    def apply_sleep_prevention(self):
        if not hasattr(ctypes, "windll"):
            return

        flags = self.ES_CONTINUOUS
        if self.prevent_sleep_var.get():
            flags |= self.ES_SYSTEM_REQUIRED | self.ES_DISPLAY_REQUIRED

        ctypes.windll.kernel32.SetThreadExecutionState(flags)

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

        port = self.port_var.get().strip()
        command = self.command_var.get().strip().upper()
        csv_file = Path(self.csv_file_var.get()).expanduser()

        if not port:
            self.connection_var.set("Choose a COM port before starting.")
            return

        self.stop_event.clear()
        self.logging_active = True
        self.set_settings_enabled(False)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.connection_var.set(f"Connecting to {port}...")

        worker_args = (port, preferred_baudrate, command, interval, rotate_hours, csv_file)
        self.worker_thread = threading.Thread(target=self.logging_worker, args=worker_args, daemon=True)
        self.worker_thread.start()

    def stop_logging(self):
        if not self.logging_active:
            return

        self.stop_event.set()
        self.connection_var.set("Stopping logger...")
        self.stop_button.configure(state="disabled")

    def logging_worker(self, port, preferred_baudrate, command, interval, rotate_hours, csv_file):
        session = LoggerSession(port, preferred_baudrate, command, csv_file, rotate_hours=rotate_hours)
        try:
            details = session.open()
            self.message_queue.put({"type": "connected", **details})

            while not self.stop_event.is_set():
                try:
                    sample = session.read_sample()
                    self.message_queue.put({"type": "sample", **sample})
                except Exception as ex:
                    self.message_queue.put({"type": "error", "message": str(ex)})

                if self.stop_event.wait(interval):
                    break
        except Exception as ex:
            self.message_queue.put({"type": "fatal", "message": str(ex)})
        finally:
            session.close()
            self.message_queue.put({"type": "stopped"})

    def process_queue(self):
        while True:
            try:
                message = self.message_queue.get_nowait()
            except queue.Empty:
                break

            message_type = message["type"]

            if message_type == "connected":
                self.csv_file_var.set(message["csv_path"])
                self.connection_var.set(
                    f"Connected to {message['port']} at {message['baudrate']} baud | Logging to {Path(message['csv_path']).name}"
                )
            elif message_type == "sample":
                self.handle_sample(message)
            elif message_type == "error":
                self.connection_var.set(f"Read error: {message['message']}")
            elif message_type == "fatal":
                self.connection_var.set(f"Connection failed: {message['message']}")
            elif message_type == "stopped":
                self.logging_active = False
                self.set_settings_enabled(True)
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                if not self.connection_var.get().startswith("Connection failed"):
                    self.connection_var.set("Logger stopped")

        self.root.after(150, self.process_queue)

    def handle_sample(self, sample):
        pressure = sample["pressure"]
        pressure_display = "-" if pressure is None else f"{pressure:.6g}"
        rotation = sample.get("rotated")

        self.pressure_var.set(pressure_display)
        self.status_var.set(sample["status"])
        self.status_meaning_var.set(sample["meaning"])
        self.last_update_var.set(sample["timestamp"])

        if rotation:
            self.csv_file_var.set(rotation["csv_path"])
            self.connection_var.set(
                f"Rotated log file | New CSV: {Path(rotation['csv_path']).name}"
            )

        self.table.insert(
            "",
            0,
            values=(
                sample["timestamp"],
                sample["command"],
                sample["status"],
                sample["meaning"],
                pressure_display,
                sample["raw"],
            ),
        )

        rows = self.table.get_children()
        if len(rows) > MAX_LOG_LINES:
            self.table.delete(*rows[MAX_LOG_LINES:])

        if pressure is not None:
            self.samples.append((sample["timestamp"], pressure))
            self.redraw_chart()

    def redraw_chart(self):
        canvas = self.chart_canvas
        canvas.delete("all")

        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 20 or height < 20:
            return

        pad_left = 48
        pad_right = 16
        pad_top = 18
        pad_bottom = 28
        plot_width = width - pad_left - pad_right
        plot_height = height - pad_top - pad_bottom

        canvas.create_rectangle(pad_left, pad_top, width - pad_right, height - pad_bottom, outline="#d7e1ea")

        if len(self.samples) < 2:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Start logging to see the pressure trend",
                fill="#6d7f90",
                font=("Segoe UI", 12),
            )
            return

        values = [value for _, value in self.samples]
        min_value = min(values)
        max_value = max(values)
        if min_value == max_value:
            min_value -= 1
            max_value += 1

        for step in range(5):
            y = pad_top + (plot_height * step / 4)
            canvas.create_line(pad_left, y, width - pad_right, y, fill="#edf2f6")
            value = max_value - ((max_value - min_value) * step / 4)
            canvas.create_text(pad_left - 8, y, text=f"{value:.3g}", fill="#607385", font=("Segoe UI", 9), anchor="e")

        points = []
        span = len(values) - 1
        for index, value in enumerate(values):
            x = pad_left + (plot_width * index / span)
            y_ratio = (value - min_value) / (max_value - min_value)
            y = pad_top + plot_height - (plot_height * y_ratio)
            points.extend([x, y])

        canvas.create_line(points, fill="#0f7c82", width=2.5, smooth=True)
        last_x, last_y = points[-2], points[-1]
        canvas.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4, fill="#0f7c82", outline="")

        first_label = self.samples[0][0][-8:]
        last_label = self.samples[-1][0][-8:]
        canvas.create_text(pad_left, height - 10, text=first_label, fill="#607385", font=("Segoe UI", 9), anchor="w")
        canvas.create_text(width - pad_right, height - 10, text=last_label, fill="#607385", font=("Segoe UI", 9), anchor="e")

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

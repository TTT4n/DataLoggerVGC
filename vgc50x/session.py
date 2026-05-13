import csv
from datetime import datetime, timedelta
from pathlib import Path

from vgc50x.protocol import connect_working_serial, parse_pressure_response, read_pressure, status_meaning

_CSV_HEADER = ["Timestamp", "Command", "Status", "StatusMeaning", "Pressure", "RawResponse"]


def build_session_csv_path(base_path):
    base_path = Path(base_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = base_path.stem or "vgc50x_pressure_log"
    suffix = base_path.suffix or ".csv"
    return base_path.with_name(f"{stem}_{timestamp}{suffix}")


class LoggerSession:
    def __init__(self, port, preferred_baudrate, command, csv_file, rotate_hours=2):
        self.port = port
        self.preferred_baudrate = preferred_baudrate
        self.command = command
        self.base_csv_path = Path(csv_file)
        self.rotate_hours = rotate_hours
        self.csv_path = None
        self.serial_connection = None
        self.active_baudrate = None
        self.probe_response = None
        self._csv_handle = None
        self._writer = None
        self._opened_at = None
        self._rotate_after = None

    def _open_csv_file(self):
        self.csv_path = build_session_csv_path(self.base_csv_path)
        is_new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        self._csv_handle = self.csv_path.open(mode="a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._csv_handle)
        if is_new:
            self._writer.writerow(_CSV_HEADER)
            self._csv_handle.flush()
        self._opened_at = datetime.now()
        self._rotate_after = self._opened_at + timedelta(hours=self.rotate_hours)

    def _close_csv_file(self):
        if self._csv_handle is not None:
            self._csv_handle.close()
            self._csv_handle = None
            self._writer = None

    def _should_rotate_csv(self):
        return self._rotate_after is not None and datetime.now() >= self._rotate_after

    def _rotate_csv_if_needed(self):
        if not self._should_rotate_csv():
            return None

        previous_csv_path = self.csv_path
        self._close_csv_file()
        self._open_csv_file()
        return {
            "previous_csv_path": str(previous_csv_path.resolve()),
            "csv_path": str(self.csv_path.resolve()),
        }

    def open(self):
        self.serial_connection, self.active_baudrate, self.probe_response = connect_working_serial(
            self.port,
            self.preferred_baudrate,
            self.command,
        )
        self._open_csv_file()
        return {
            "port": self.port,
            "baudrate": self.active_baudrate,
            "probe": self.probe_response,
            "csv_path": str(self.csv_path.resolve()),
        }

    def read_sample(self):
        rotation = self._rotate_csv_if_needed()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw = read_pressure(self.serial_connection, self.command)
        data = parse_pressure_response(raw)
        meaning = status_meaning(data["status"])

        sample = {
            "timestamp": timestamp,
            "command": self.command,
            "status": data["status"],
            "meaning": meaning,
            "pressure": data["pressure"],
            "raw": data["raw"],
            "rotated": rotation,
        }

        try:
            self._writer.writerow(
                [
                    sample["timestamp"],
                    sample["command"],
                    sample["status"],
                    sample["meaning"],
                    sample["pressure"],
                    sample["raw"],
                ]
            )
            self._csv_handle.flush()
        except OSError as ex:
            raise RuntimeError(f"Failed to write CSV data to {self.csv_path}: {ex}") from ex

        return sample

    def close(self):
        if self.serial_connection is not None and self.serial_connection.is_open:
            self.serial_connection.close()
        self._close_csv_file()

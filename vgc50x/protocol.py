import time

import serial
from serial.tools import list_ports

from vgc50x.config import ACK, AUTO_DETECT_BAUDRATE, BAUDRATE_CANDIDATES, ENQ, NAK, SERIAL_TIMEOUT_SEC


def wait_for_ack(ser, timeout_sec=SERIAL_TIMEOUT_SEC):
    """
    Wait for ACK / NAK while tolerating leftover ASCII measurement lines.
    """
    deadline = time.monotonic() + timeout_sec
    received = bytearray()

    while time.monotonic() < deadline:
        chunk = ser.read(1)
        if not chunk:
            continue

        received.extend(chunk)

        if chunk == ACK:
            trailer = ser.read_until(b"\n", size=8)
            received.extend(trailer)
            return bytes(received)

        if chunk == NAK:
            trailer = ser.read_until(b"\n", size=32)
            received.extend(trailer)
            raise RuntimeError(f"Device returned NAK. Response={bytes(received)!r}")

    raise RuntimeError(f"No ACK from device within {timeout_sec}s. Response={bytes(received)!r}")


def read_pressure(ser, command):
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write((command + "\r\n").encode("ascii"))
    wait_for_ack(ser)
    ser.write(ENQ)
    response = ser.readline().decode("ascii", errors="ignore").strip()

    if not response:
        raise RuntimeError("Device returned an empty pressure response.")

    return response


def parse_pressure_response(response):
    parts = response.split(",")

    if len(parts) < 2:
        return {"status": "PARSE_ERROR", "pressure": None, "raw": response}

    status = parts[0].strip()
    pressure_text = parts[1].strip()

    try:
        pressure = float(pressure_text)
    except ValueError:
        pressure = None

    return {"status": status, "pressure": pressure, "raw": response}


def status_meaning(status):
    meanings = {
        "0": "OK",
        "1": "Underrange",
        "2": "Overrange",
        "3": "Sensor error",
        "4": "Sensor off",
        "5": "No sensor",
        "6": "Identification error",
        "7": "Gauge error",
    }
    return meanings.get(status, "Unknown")


def open_serial(port, baudrate):
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=SERIAL_TIMEOUT_SEC,
    )


def candidate_baudrates(preferred_baudrate):
    ordered = [preferred_baudrate]
    if AUTO_DETECT_BAUDRATE:
        ordered.extend(rate for rate in BAUDRATE_CANDIDATES if rate != preferred_baudrate)

    seen = set()
    result = []
    for rate in ordered:
        if rate not in seen:
            seen.add(rate)
            result.append(rate)
    return result


def connect_working_serial(port, preferred_baudrate, command):
    last_error = None

    for baudrate in candidate_baudrates(preferred_baudrate):
        try:
            ser = open_serial(port, baudrate)
            time.sleep(0.2)

            try:
                probe = read_pressure(ser, command)
                return ser, baudrate, probe
            except Exception as ex:
                last_error = ex
                ser.close()
        except Exception as ex:
            last_error = ex

    raise RuntimeError(
        f"Unable to communicate with VGC50x on {port} "
        f"using baudrates {candidate_baudrates(preferred_baudrate)}. Last error: {last_error}"
    )


def list_serial_port_names():
    return sorted(port.device for port in list_ports.comports())


# VGC50x Data Logger

Desktop data logger for an INFICON VGC50x vacuum gauge controller.

## Features

- live pressure graph
- COM port, baudrate, and command selection
- CSV logging
- automatic new CSV creation for each session
- optional CSV rotation every N hours
- recent samples table
- Windows keep-awake option while the app is open

## Project Layout

```text
VGC50x/
|-- vgc50x/
|   |-- __init__.py
|   |-- config.py
|   |-- protocol.py
|   |-- session.py
|   `-- ui.py
|-- vgc50x_logger.py
|-- build.ps1
|-- requirements.txt
`-- README.md
```

## Requirements

- Windows
- Python 3
- VGC50x connected through its virtual COM port

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Run From Python

```powershell
.\.venv\Scripts\python.exe .\vgc50x_logger.py
```

## Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The build creates a timestamped executable in `dist\`, for example:

```text
dist\DataloggerVGC_realtime_20260513_091749.exe
```

## GUI Notes

- `Record every (seconds)` means how often one sample is recorded.
  Example: `60` means one sample every 1 minute.
- `New CSV every (hours)` controls log rotation.
  Example: `2` means create a new CSV every 2 hours while logging.
- Settings are locked while logging is active and unlocked again after stop.

## Serial Protocol

The logger uses the VGC50x request / response flow:

1. Send a command such as `PR1`
2. Wait for `ACK`
3. Send `ENQ`
4. Read the ASCII pressure response

Official reference:

- INFICON Operating Manual, `Section 5 Communication Protocol (Serial Interface)`, starting on page 67
- `5.1 Data Transmission` starts on page 68
- `5.2 Communication Protocol` starts on page 69
- Manual PDF: https://www.inficon.com/media/4375/download/Operating-manual-VGC50x.pdf?v=3&inline=true&language=es

## Troubleshooting

- If no data appears, confirm the correct COM port in Device Manager.
- If the port opens but readings fail, check the controller baudrate.
- If another program is using the same COM port, close it first.
- If EXE building fails inside a restricted environment, run the build with the PowerShell command above.

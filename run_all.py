import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


"""
只启动 6 个后端服务：

.\.venv\Scripts\python.exe run_all.py

连 Streamlit 前端一起启动：
.\.venv\Scripts\python.exe run_all.py --with-ui

"""



ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


SERVICES = [
    ("mcp-ticket", [PYTHON, "-u", "mcp_server/mcp_ticket_server.py"]),
    ("mcp-weather", [PYTHON, "-u", "mcp_server/mcp_weather_server.py"]),
    ("mcp-order", [PYTHON, "-u", "mcp_server/mcp_order_server.py"]),
    ("a2a-ticket", [PYTHON, "-u", "a2a_server/ticket_server.py"]),
    ("a2a-weather", [PYTHON, "-u", "a2a_server/weather_server.py"]),
    ("a2a-order", [PYTHON, "-u", "a2a_server/order_server.py"]),
]

UI_SERVICE = ("streamlit", [PYTHON, "-m", "streamlit", "run", "app.py"])


def stream_output(name: str, process: subprocess.Popen) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{name}] {line.rstrip()}")


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def start_process(name: str, command: list[str]) -> tuple[subprocess.Popen, threading.Thread]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    thread = threading.Thread(target=stream_output, args=(name, process), daemon=True)
    thread.start()
    return process, thread


def main() -> int:
    parser = argparse.ArgumentParser(description="Start SmartVoyage services.")
    parser.add_argument(
        "--with-ui",
        action="store_true",
        help="Also start the Streamlit frontend.",
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=1.0,
        help="Delay in seconds between process starts.",
    )
    args = parser.parse_args()

    processes: list[tuple[str, subprocess.Popen]] = []

    try:
        for name, command in SERVICES:
            process, _ = start_process(name, command)
            processes.append((name, process))
            print(f"[launcher] started {name}")
            time.sleep(args.startup_delay)

        if args.with_ui:
            process, _ = start_process(*UI_SERVICE)
            processes.append((UI_SERVICE[0], process))
            print("[launcher] started streamlit")

        print("[launcher] all requested processes started, press Ctrl+C to stop")

        while True:
            failed = [(name, proc.returncode) for name, proc in processes if proc.poll() is not None]
            if failed:
                for name, returncode in failed:
                    print(f"[launcher] {name} exited with code {returncode}")
                return 1
            time.sleep(1)

    except KeyboardInterrupt:
        print("[launcher] stopping processes")
        return 0
    finally:
        for _, process in reversed(processes):
            terminate_process(process)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())

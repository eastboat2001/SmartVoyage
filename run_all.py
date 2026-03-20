import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


r"""
只启动 6 个后端服务：

.\.venv\Scripts\python.exe run_all.py

连 Streamlit 前端一起启动：
.\.venv\Scripts\python.exe run_all.py --with-ui

连命令行入口一起启动：
.\.venv\Scripts\python.exe run_all.py --with-cli

"""



ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
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
CLI_SERVICE = ("main-cli", [PYTHON, "main.py"])

def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def prepare_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for log_file in LOG_DIR.glob("*.log"):
        log_file.write_text("", encoding="utf-8")


def log_path_for(name: str) -> Path:
    if name.startswith("mcp-"):
        return LOG_DIR / "mcp.log"
    if name.startswith("a2a-"):
        return LOG_DIR / "a2a.log"
    return LOG_DIR / f"{name}.log"


def start_process(name: str, command: list[str]) -> tuple[subprocess.Popen, Path]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    log_path = log_path_for(name)
    env["SMARTVOYAGE_LOG_FILE"] = str(LOG_DIR / "app.log")
    log_handle = open(log_path, "a", encoding="utf-8")

    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
    finally:
        log_handle.close()
    return process, log_path


def start_interactive_process(name: str, command: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SMARTVOYAGE_LOG_FILE"] = str(LOG_DIR / "app.log")
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
    )


def run_interactive_process(name: str, command: list[str]) -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SMARTVOYAGE_LOG_FILE"] = str(LOG_DIR / "app.log")
    print(f"[launcher] entering {name}, press Ctrl+C to stop")
    return subprocess.call(
        command,
        cwd=ROOT,
        env=env,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Start SmartVoyage services.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--with-ui",
        action="store_true",
        help="Also start the Streamlit frontend.",
    )
    mode_group.add_argument(
        "--with-cli",
        action="store_true",
        help="Also start the command-line entrypoint.",
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=1.0,
        help="Delay in seconds between process starts.",
    )
    args = parser.parse_args()

    prepare_log_dir()
    processes: list[tuple[str, subprocess.Popen, Path]] = []

    try:
        for name, command in SERVICES:
            process, log_path = start_process(name, command)
            processes.append((name, process, log_path))
            print(f"[launcher] started {name} -> {log_path.relative_to(ROOT)}")
            time.sleep(args.startup_delay)

        if args.with_ui:
            process = start_interactive_process(*UI_SERVICE)
            processes.append((UI_SERVICE[0], process, ROOT / "CONSOLE"))
            print("[launcher] started streamlit in current terminal")

        print("[launcher] all requested processes started, press Ctrl+C to stop")
        print("[launcher] mcp service output -> logs/mcp.log")
        print("[launcher] a2a service output -> logs/a2a.log")
        print("[launcher] application logger -> logs/app.log")

        if args.with_cli:
            return run_interactive_process(*CLI_SERVICE)

        while True:
            failed = [
                (name, proc.returncode, log_path)
                for name, proc, log_path in processes
                if proc.poll() is not None
            ]
            if failed:
                for name, returncode, log_path in failed:
                    if log_path.name == "CONSOLE":
                        print(f"[launcher] {name} exited with code {returncode}.")
                    else:
                        print(
                            f"[launcher] {name} exited with code {returncode}. "
                            f"See {log_path.relative_to(ROOT)} for details."
                        )
                return 1
            time.sleep(1)

    except KeyboardInterrupt:
        print("[launcher] stopping processes")
        return 0
    finally:
        for _, process, _ in reversed(processes):
            terminate_process(process)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())

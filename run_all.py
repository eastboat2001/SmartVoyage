import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


r"""
只启动后端服务：

.\.venv\Scripts\python.exe run_all.py

连命令行入口一起启动：
.\.venv\Scripts\python.exe run_all.py --with-cli

连 FastAPI 前端页面一起启动：
.\.venv\Scripts\python.exe run_all.py --with-web

同时启动后端、Web 页面和 CLI：
.\.venv\Scripts\python.exe run_all.py --with-web --with-cli
"""


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PYTHON = sys.executable


SERVICES = [
    ("mcp-travel-read", [PYTHON, "-u", "mcp_server/mcp_travel_read_server.py"]),
    ("mcp-order", [PYTHON, "-u", "mcp_server/mcp_order_server.py"]),
    ("a2a-travel-decision", [PYTHON, "-u", "a2a_server/travel_decision_server.py"]),
    ("a2a-order", [PYTHON, "-u", "a2a_server/order_server.py"]),
]

WEB_SERVICE = ("web-ui", [PYTHON, "-u", "web_app.py"])
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
    if name.startswith("web-"):
        return LOG_DIR / "web.log"
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
    parser.add_argument(
        "--with-cli",
        action="store_true",
        help="Also start the command-line entrypoint.",
    )
    parser.add_argument(
        "--with-web",
        action="store_true",
        help="Also start the FastAPI web frontend on http://127.0.0.1:8501.",
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

        if args.with_web:
            process, log_path = start_process(*WEB_SERVICE)
            processes.append((WEB_SERVICE[0], process, log_path))
            print(f"[launcher] started {WEB_SERVICE[0]} -> {log_path.relative_to(ROOT)}")
            print("[launcher] web frontend -> http://127.0.0.1:8501")
            time.sleep(args.startup_delay)

        print("[launcher] all requested processes started, press Ctrl+C to stop")
        print("[launcher] mcp service output -> logs/mcp.log")
        print("[launcher] a2a service output -> logs/a2a.log")
        print("[launcher] application logger -> logs/app.log")
        if args.with_web:
            print("[launcher] web frontend output -> logs/web.log")

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

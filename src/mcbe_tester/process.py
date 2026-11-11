import os
import signal
import subprocess
import time


def run_bounded(command, cwd, log_path, stop_file, timeout):
    """Terminate the subprocess tree on timeout/STOP so a spawned BDS is not left running."""
    if stop_file.exists():
        log_path.write_text("Stopped before starting the external tool\n", encoding="utf-8")
        return {"exit_code": None, "status": "blocked", "reason": "Stopped by user"}
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with log_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, **options)
        deadline = time.monotonic() + timeout
        reason = None
        try:
            while process.poll() is None:
                if stop_file.exists():
                    reason = "Stopped by user"
                elif time.monotonic() >= deadline:
                    reason = "External tool timed out"
                elif log_path.stat().st_size > 32 * 1024 * 1024:
                    reason = "External tool output exceeded 32 MiB"
                if reason:
                    break
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        check=False,
                    )
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=10)
        return {
            "exit_code": process.returncode,
            "status": "blocked" if reason else "completed",
            "reason": reason,
        }

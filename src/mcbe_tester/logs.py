import json
import os
import re
from pathlib import Path

ERROR = re.compile(r"\[(?:error|fatal)\]", re.I)


def default_log_dirs():
    roots = []
    if os.getenv("APPDATA"):
        roots.append(str(Path(os.environ["APPDATA"]) / "Minecraft Bedrock" / "logs"))
    if os.getenv("LOCALAPPDATA"):
        roots.append(
            str(
                Path(os.environ["LOCALAPPDATA"])
                / "Packages"
                / "Microsoft.MinecraftUWP_8wekyb3d8bbwe"
                / "LocalState"
                / "logs"
            )
        )
    return roots


def log_files(directories):
    return sorted({p.resolve() for d in directories for p in Path(d).glob("*.log") if p.is_file()})


def cursors(directories):
    return {str(p): {"offset": p.stat().st_size, "pending": ""} for p in log_files(directories)}


def tail(directories, positions, run_id, started_epoch):
    lines, telemetry, errors = [], [], []
    truncated = False
    for path in log_files(directories):
        key = str(path)
        if key not in positions and path.stat().st_mtime < started_epoch:
            continue
        state = positions.setdefault(key, {"offset": 0, "pending": ""})
        if path.stat().st_size < state["offset"]:
            state.update(offset=0, pending="")
        with path.open("rb") as stream:
            stream.seek(state["offset"])
            data = stream.read(4 * 1024 * 1024)
            state["offset"] = stream.tell()
            truncated |= bool(stream.read(1))
        # Bedrock logs are UTF-8. Preserve incomplete UTF-8 sequences between reads.
        pending = bytes.fromhex(state["pending"]) + data
        pieces = pending.split(b"\n")
        state["pending"] = pieces.pop().hex()
        if len(state["pending"]) > 2 * 1024 * 1024:
            raise ValueError("A content log line exceeds 1 MiB")
        for raw in pieces:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            lines.append({"file": key, "text": line})
            if "BEDROCKLAB " in line:
                try:
                    value, _ = json.JSONDecoder().raw_decode(line.split("BEDROCKLAB ", 1)[1])
                    if isinstance(value, dict) and value.get("runId") == run_id:
                        telemetry.append(value)
                        if value.get("kind") == "observer_error":
                            errors.append({"file": key, "text": line})
                except ValueError:
                    errors.append({"file": key, "text": "Malformed observer telemetry: " + line})
            elif ERROR.search(line):
                errors.append({"file": key, "text": line})
    return {
        "lines": lines,
        "telemetry": telemetry,
        "errors": errors,
        "more_available": truncated,
        "note": "No error lines is not proof of success. Observer data is filtered by run ID.",
    }

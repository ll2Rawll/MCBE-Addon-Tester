import io
import json
import struct
import uuid
import zipfile
from pathlib import Path

from nbtlib import Byte, File, String
from PIL import Image


def addon(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format_version": 2,
        "header": {
            "name": "Test",
            "uuid": str(uuid.uuid4()),
            "version": [1, 0, 0],
            "min_engine_version": [1, 21, 0],
        },
        "modules": [{"uuid": str(uuid.uuid4()), "type": "data", "version": [1, 0, 0]}],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def template(path, refs=None):
    nbt = File({"LevelName": String("Original"), "commandsEnabled": Byte(1)})
    stream = io.BytesIO()
    nbt.write(stream, byteorder="little")
    data = stream.getvalue()
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("level.dat", struct.pack("<II", 10, len(data)) + data)
        z.writestr("db/CURRENT", b"fixture only, not a playable LevelDB")
        z.writestr("world_behavior_packs.json", json.dumps(refs or []))
    return path


def plan(path, file_version="1.2.3.4"):
    data = {
        "schema_version": 1,
        "target": {"file_version": file_version},
        "requirements": [{"id": "visual", "category": "graphics", "expected": "A green cube <not pink>"}],
    }
    path.write_text(json.dumps(data))
    return path


class FakeDesktop:
    """Protocol fake for tests. Never represents a real-game acceptance result."""

    def __init__(self, stop_file, hwnd=None):
        self.stop_file, self.hwnd = Path(stop_file), hwnd

    def windows(self):
        return [{"hwnd": 42, "title": "Minecraft", "exe": "Minecraft.Windows.exe", "file_version": "1.2.3.4"}]

    def select(self, hwnd=None):
        self.hwnd = 42
        return self.windows()[0]

    def guard(self):
        if self.stop_file.exists():
            raise RuntimeError("Stopped")

    def capture(self, path):
        self.guard()
        Image.new("RGB", (32, 32), "green").save(path)
        return {"hwnd": self.hwnd}

    def focus(self):
        self.guard()

    def press(self, **kwargs):
        self.guard()

    def wait(self, seconds):
        self.guard()

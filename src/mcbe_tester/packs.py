"""Bounded archive inspection and disposable worlds; never writes to Minecraft's data folders."""

import io
import json
import re
import stat
import struct
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .util import sha256

MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 20000


def jsonc(data):
    """Remove // and /* */ comments outside strings, retaining newlines for errors."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    pattern = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\r\n]*'
    text = re.sub(
        pattern,
        lambda m: m[0] if m[0].startswith('"') else "".join("\n" if c == "\n" else " " for c in m[0]),
        text,
        flags=re.S,
    )
    return json.loads(text)


def safe_name(name):
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(p in (".", "..") or ":" in p for p in path.parts)
        or "\x00" in name
    ):
        raise ValueError(f"Unsafe archive path: {name!r}")
    return str(path)


def read_files(source):
    """Return bounded virtual files. Never extract an untrusted archive to disk."""
    source = Path(source)
    files = {}
    names = set()
    used = 0
    count = 0

    def add(name, data):
        key = safe_name(name)
        if key.casefold() in names:
            raise ValueError(f"Duplicate/case-conflicting path: {key}")
        names.add(key.casefold())
        files[key] = data

    def unpack(blob, prefix="", depth=0):
        nonlocal used, count
        if depth > 3:
            raise ValueError("Too many nested pack archives")
        with zipfile.ZipFile(blob) as archive:
            for info in archive.infolist():
                name = safe_name(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise ValueError("Symlinks and encrypted archives are not supported")
                if info.is_dir():
                    continue
                count += 1
                used += info.file_size
                if count > MAX_FILES or used > MAX_BYTES:
                    raise ValueError("Archive exceeds the 256 MiB / 20,000-file safety limit")
                data = archive.read(info)
                if name.lower().endswith((".mcpack", ".mcaddon")):
                    unpack(io.BytesIO(data), prefix + name + "/", depth + 1)
                else:
                    add(prefix + name, data)

    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError("Symlinks are not supported in pack folders")
            if not path.is_file():
                continue
            count += 1
            used += path.stat().st_size
            if used > MAX_BYTES or count > MAX_FILES:
                raise ValueError("Folder exceeds the 256 MiB / 20,000-file safety limit")
            add(path.relative_to(source).as_posix(), path.read_bytes())
    else:
        unpack(source)
    return files


def inventory(files):
    packs = []
    seen = set()
    for name, raw in files.items():
        if PurePosixPath(name).name != "manifest.json":
            continue
        manifest = jsonc(raw)
        header = manifest.get("header", {})
        pack_id = str(uuid.UUID(header["uuid"]))
        if pack_id in seen:
            raise ValueError(f"Duplicate pack UUID: {pack_id}")
        seen.add(pack_id)
        version = header["version"]
        if not (
            isinstance(version, list)
            and len(version) == 3
            and all(type(v) is int and v >= 0 for v in version)
        ):
            raise ValueError(f"Unsupported pack version in {name}; expected three integers")
        types = {m.get("type") for m in manifest.get("modules", [])}
        if types <= {"resources"} and types:
            kind = "resource"
        elif types <= {"data", "script"} and types:
            kind = "behavior"
        else:
            raise ValueError(f"Unsupported/mixed module types in {name}: {sorted(map(str, types))}")
        parent = str(PurePosixPath(name).parent)
        prefix = "" if parent == "." else parent + "/"
        packs.append(
            {
                "uuid": pack_id,
                "version": version,
                "name": header.get("name", pack_id),
                "kind": kind,
                "prefix": prefix,
                "manifest": manifest,
            }
        )
    if not packs:
        raise ValueError("No supported behavior/resource pack manifest.json found")
    for pack in packs:
        for other in packs:
            if other is not pack and other["prefix"].startswith(pack["prefix"]):
                raise ValueError("Nested pack roots are ambiguous; use sibling pack directories")
    return packs


def inspect_addon(source):
    files = read_files(source)
    packs = inventory(files)
    errors, warnings, content = [], [], []
    for name, data in files.items():
        if name.endswith(".json"):
            try:
                parsed = jsonc(data)
                if isinstance(parsed, dict):
                    for key in (
                        "minecraft:entity",
                        "minecraft:client_entity",
                        "minecraft:block",
                        "minecraft:item",
                    ):
                        value = parsed.get(key)
                        if isinstance(value, dict):
                            content.append(
                                {
                                    "file": name,
                                    "type": key,
                                    "identifier": value.get("description", {}).get("identifier"),
                                }
                            )
            except (ValueError, UnicodeError) as exc:
                errors.append({"file": name, "message": str(exc)})
    pack_ids = {p["uuid"] for p in packs}
    for pack in packs:
        for module in pack["manifest"].get("modules", []):
            if module.get("type") == "script":
                entry = module.get("entry", "")
                if not entry or pack["prefix"] + entry not in files:
                    errors.append(
                        {"file": pack["prefix"] + "manifest.json", "message": "Script entry missing"}
                    )
        for dep in pack["manifest"].get("dependencies", []):
            if dep.get("uuid") and dep["uuid"] not in pack_ids:
                warnings.append({"pack": pack["name"], "message": f"External dependency: {dep['uuid']}"})
    import hashlib

    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return {
        "source": str(Path(source).resolve()),
        "content_sha256": digest.hexdigest(),
        "file_count": len(files),
        "packs": packs,
        "content": content,
        "errors": errors,
        "warnings": warnings,
        "scope": "Basic structure and syntax only; not full Bedrock schema validation",
    }


def build_world(template, addon, destination, run_id, api_version=None):
    """Build a new .mcworld from a clean exported template and unchanged add-on files."""
    from nbtlib import File, String

    world = read_files(template)
    if "level.dat" not in world or not any(p.startswith("db/") for p in world):
        raise ValueError("Template must be an exported .mcworld with level.dat and db/ at its root")
    for name in world:
        if name.startswith(("behavior_packs/", "resource_packs/")):
            raise ValueError("Use a clean template with no embedded packs")
    for ref in ("world_behavior_packs.json", "world_resource_packs.json"):
        if jsonc(world.get(ref, b"[]")):
            raise ValueError("Template has active packs; export a clean world first")
    raw = world["level.dat"]
    if len(raw) < 8:
        raise ValueError("Truncated Bedrock level.dat")
    storage_version, length = struct.unpack("<II", raw[:8])
    if length != len(raw) - 8:
        raise ValueError("Unexpected Bedrock level.dat length")
    nbt = File.parse(io.BytesIO(raw[8:]), byteorder="little")
    name = "Addon Lab " + run_id
    nbt["LevelName"] = String(name)
    buffer = io.BytesIO()
    nbt.write(buffer, byteorder="little")
    data = buffer.getvalue()
    world["level.dat"] = struct.pack("<II", storage_version, len(data)) + data
    world["levelname.txt"] = name.encode()
    world.pop("level.dat_old", None)
    files = read_files(addon)
    packs = inventory(files)
    refs = {"behavior": [], "resource": []}
    for pack in packs:
        prefix = pack["prefix"]
        for path, data in files.items():
            if path.startswith(prefix):
                world[f"{pack['kind']}_packs/{pack['uuid']}/{path[len(prefix) :]}"] = data
        refs[pack["kind"]].append({"pack_id": pack["uuid"], "version": pack["version"]})
    if api_version:
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", api_version):
            raise ValueError("Provide an exact @minecraft/server API version supported by the target game")
        pack_id = str(uuid.uuid4())
        root = f"behavior_packs/{pack_id}/"
        manifest = {
            "format_version": 2,
            "header": {
                "name": "Addon Lab Observer",
                "description": run_id,
                "uuid": pack_id,
                "version": [1, 0, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "type": "script",
                    "language": "javascript",
                    "entry": "scripts/main.js",
                    "uuid": str(uuid.uuid4()),
                    "version": [1, 0, 0],
                }
            ],
            "dependencies": [{"module_name": "@minecraft/server", "version": api_version}],
        }
        world[root + "manifest.json"] = json.dumps(manifest).encode()
        script = (Path(__file__).parent / "assets" / "observer.js").read_text(encoding="utf-8")
        world[root + "scripts/main.js"] = script.replace("__RUN_ID__", json.dumps(run_id)).encode()
        refs["behavior"].append({"pack_id": pack_id, "version": [1, 0, 0]})
    for kind, values in refs.items():
        world[f"world_{kind}_packs.json"] = json.dumps(values).encode()
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing test world export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in world.items():
            archive.writestr(path, data)
    return {
        "path": str(destination.resolve()),
        "sha256": sha256(destination),
        "name": name,
        "observer": bool(api_version),
        "api_version": api_version,
        "note": "Template experiments/settings preserved. Import and test the world in Minecraft.",
    }

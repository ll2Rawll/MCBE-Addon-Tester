import io
import json
import stat
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from nbtlib import File

from mcbe_tester import packs
from tests.helpers import addon, template


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_json_comments_do_not_destroy_urls_or_escaped_strings(self):
        self.assertEqual(
            packs.jsonc('{"url":"https://site/a/*b*/",/* hi */ "n":1 // ok\n}'),
            {"url": "https://site/a/*b*/", "n": 1},
        )

    def test_basic_inventory_and_content_hash(self):
        source = addon(self.root / "addon")
        original = packs.inspect_addon(source)
        (source / "invalid.json").write_text('{"broken":')
        current = packs.inspect_addon(source)
        self.assertNotEqual(original["content_sha256"], current["content_sha256"])
        self.assertEqual(len(current["errors"]), 1)

    def test_nested_mcpack(self):
        source = addon(self.root / "addon")
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("manifest.json", (source / "manifest.json").read_bytes())
        outer = self.root / "addon.mcaddon"
        with zipfile.ZipFile(outer, "w") as z:
            z.writestr("test.mcpack", inner.getvalue())
        self.assertEqual(len(packs.inspect_addon(outer)["packs"]), 1)

    def test_path_traversal_and_absolute_paths_rejected(self):
        for name in ("../escape", "/absolute", "C:/escape", "..\\escape"):
            with self.subTest(name=name):
                archive = self.root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as z:
                    z.writestr(name, "bad")
                with self.assertRaises(ValueError):
                    packs.read_files(archive)

    def test_symlinks_and_case_collisions_rejected(self):
        archive = self.root / "bad.zip"
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(info, "target")
        with self.assertRaises(ValueError):
            packs.read_files(archive)
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("A.json", "{}")
            z.writestr("a.json", "{}")
        with self.assertRaises(ValueError):
            packs.read_files(archive)

    def test_decompression_budget_enforced(self):
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big", b"x" * 100)
        with patch.object(packs, "MAX_BYTES", 50), self.assertRaises(ValueError):
            packs.read_files(archive)

    def test_world_preserves_pack_bytes_and_settings(self):
        source = addon(self.root / "addon")
        base = template(self.root / "base.mcworld")
        before = base.read_bytes()
        output = self.root / "run.mcworld"
        result = packs.build_world(base, source, output, "run-123", "2.0.0")
        self.assertEqual(base.read_bytes(), before)
        with zipfile.ZipFile(output) as z:
            raw = z.read("level.dat")
            self.assertEqual(struct.unpack("<II", raw[:8]), (10, len(raw) - 8))
            nbt = File.parse(io.BytesIO(raw[8:]), byteorder="little")
            self.assertEqual(nbt["LevelName"], "Addon Lab run-123")
            self.assertEqual(nbt["commandsEnabled"], 1)
            refs = json.loads(z.read("world_behavior_packs.json"))
            self.assertEqual(len(refs), 2)
            self.assertEqual(
                z.read(f"behavior_packs/{refs[0]['pack_id']}/manifest.json"),
                (source / "manifest.json").read_bytes(),
            )
            script = z.read(f"behavior_packs/{refs[1]['pack_id']}/scripts/main.js")
            self.assertIn(b'"run-123"', script)
        self.assertTrue(result["observer"])

    def test_template_with_existing_packs_is_rejected(self):
        source = addon(self.root / "addon")
        base = template(self.root / "base.mcworld", [{"pack_id": "other"}])
        with self.assertRaises(ValueError):
            packs.build_world(base, source, self.root / "out.mcworld", "run")

    def test_duplicate_uuid_rejected(self):
        first = addon(self.root / "first")
        second = self.root / "second"
        second.mkdir()
        (second / "manifest.json").write_bytes((first / "manifest.json").read_bytes())
        with self.assertRaises(ValueError):
            packs.inspect_addon(self.root)

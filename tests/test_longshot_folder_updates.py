"""Regression tests for 3-channel Longshot discovery, folder normalization, and party migration."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oot_rando import pc_port
from oot_rando.parties import PartyLibrary
from oot_rando.patching.randomize_kokiri import BUILD, TITLE
from oot_rando.randomizer.randomizer_core import ADAPTER_VERSION, GENERATOR_VERSION, Settings, settings_identity


def _make_port(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "image.bin").write_bytes(b"img")
    (root / "romfs").mkdir(parents=True, exist_ok=True)
    (root / "mods").mkdir(parents=True, exist_ok=True)
    return root


def _make_fake_seed(seed_dir: Path, seed_label: str = "testseed") -> str:
    settings = Settings(seed=seed_label)
    seed_id = settings_identity(settings)
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / "romfs").mkdir(exist_ok=True)
    data = seed_label.encode("utf-8")
    (seed_dir / "romfs" / "resource").write_bytes(data)
    manifest = {
        "schema_version": 1,
        "title_id": TITLE,
        "build_id": BUILD,
        "seed_id": seed_id,
        "generator_version": GENERATOR_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "settings": settings.to_dict(),
        "files": {
            "resource": {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        },
    }
    (seed_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (seed_dir / "checks.json").write_text(json.dumps({"seed_id": seed_id, "checks": []}), encoding="utf-8")
    return seed_id


class LongshotFolderUpdateTests(unittest.TestCase):
    def test_single_channel_upgrade_from_deleted_v3_1_1_to_v3_2_0(self):
        with tempfile.TemporaryDirectory() as td:
            ls_root = Path(td) / "Longshot"
            v320 = _make_port(ls_root / "stable" / "v3.2.0")
            stale_mods = ls_root / "stable" / "v3.1.1" / "mods"

            info = pc_port.resolve_longshot_update(stale_mods)
            self.assertEqual(info["status"], "upgraded")
            self.assertEqual(info["current_channel"], "stable")
            self.assertEqual(info["recommended"]["port_dir"], v320)
            self.assertEqual(info["recommended"]["mods_dir"], v320 / "mods")

            same = pc_port.resolve_longshot_update(v320 / "mods")
            self.assertEqual(same["status"], "unchanged")

    def test_multi_channel_lists_stable_rc_and_nightly(self):
        with tempfile.TemporaryDirectory() as td:
            ls_root = Path(td) / "Longshot"
            _make_port(ls_root / "stable" / "v3.2.0")
            _make_port(ls_root / "release candidate" / "v3.2.1-rc.2")
            _make_port(ls_root / "release candidate" / "v3.2.1-rc.1")
            _make_port(ls_root / "nightly" / "2026-11-24")

            info = pc_port.resolve_longshot_update(ls_root / "stable" / "v3.1.1" / "mods")
            self.assertEqual(info["status"], "choose_channel")
            self.assertEqual(
                [(c["channel"], c["version_name"]) for c in info["channels"]],
                [
                    ("stable", "v3.2.0"),
                    ("release candidate", "v3.2.1-rc.2"),
                    ("nightly", "2026-11-24"),
                ],
            )

    def test_normalize_port_mods_dir_resolves_port_root(self):
        with tempfile.TemporaryDirectory() as td:
            v320 = _make_port(Path(td) / "Longshot" / "stable" / "v3.2.0")
            self.assertEqual(pc_port.normalize_port_mods_dir(str(v320) + os.sep), str(v320 / "mods"))
            self.assertEqual(pc_port.normalize_port_mods_dir(str(v320 / "mods") + os.sep), str(v320 / "mods"))

    def test_party_migration_and_activation_when_outgoing_port_removed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ls_root = base / "Longshot"
            v311 = _make_port(ls_root / "stable" / "v3.1.1")
            v320 = _make_port(ls_root / "stable" / "v3.2.0")
            saves = base / "saves" / "account01"
            saves.mkdir(parents=True)

            lib = PartyLibrary(base / "Parties")
            seed1 = base / "tmp_seed1"
            _make_fake_seed(seed1, "oldpartyseed")
            with mock.patch.object(lib, "_closed"):
                p1 = lib.create("OldParty", seed1, v311 / "mods", saves)
                lib.activate(p1["id"])
            (saves / "slot_00").mkdir(parents=True)
            (saves / "slot_00" / "progress.sav").write_bytes(b"sav")

            shutil.rmtree(v311)

            seed2 = base / "tmp_seed2"
            _make_fake_seed(seed2, "newpartyseed")
            with mock.patch.object(lib, "_closed"):
                p2 = lib.create("NewParty", seed2, v320 / "mods", saves)
                lib.activate(p2["id"])
            self.assertEqual(lib.active_id, p2["id"])

            migrated = lib.migrate_mods_directory(v320 / "mods", old_mods=v311 / "mods", root=ls_root)
            self.assertEqual(migrated, ["OldParty"])
            self.assertEqual(Path(lib.get(p1["id"])["mods"]), (v320 / "mods").resolve())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from meta_pcvr_downloader.api import Build, parse_app_id, select_build
from meta_pcvr_downloader.download import Downloader, DownloadError, _safe_destination


class ApiTests(unittest.TestCase):
    def test_parse_numeric_and_store_url(self):
        self.assertEqual(parse_app_id("2031736060288351"), "2031736060288351")
        self.assertEqual(
            parse_app_id(
                "https://www.meta.com/experiences/pcvr/title/2031736060288351/"
            ),
            "2031736060288351",
        )

    def test_select_build(self):
        builds = [
            Build("1", "Game", "30", "3.0", 30),
            Build("1", "Game", "20", "2.0", 20),
        ]
        self.assertEqual(select_build(builds, None).binary_id, "30")
        self.assertEqual(select_build(builds, "2.0").binary_id, "20")


class DownloadTests(unittest.TestCase):
    def test_manifest_path_traversal_is_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaises(DownloadError),
        ):
            _safe_destination(Path(directory), "../escape.exe")

    def test_duplicate_segment_is_assembled_into_multiple_files(self):
        clear = b"shared segment"
        segment_hash = hashlib.sha256(clear).hexdigest()
        file_hash = hashlib.sha256(clear).hexdigest()
        manifest = {
            "launchFile": "one.bin",
            "files": {
                "one.bin": {
                    "size": len(clear),
                    "sha256": file_hash,
                    "segments": [[0, segment_hash]],
                },
                "sub/two.bin": {
                    "size": len(clear),
                    "sha256": file_hash,
                    "segments": [[0, segment_hash]],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            output = root / "game"
            cache.mkdir()
            (cache / segment_hash).write_bytes(clear)
            build = Build("1", "Test", "2", "1.0", 1)
            Downloader("token", build, output, cache, jobs=1).run(manifest)
            self.assertEqual((output / "one.bin").read_bytes(), clear)
            self.assertEqual((output / "sub/two.bin").read_bytes(), clear)
            self.assertEqual(
                json.loads((output / "manifest.json").read_text())["launchFile"],
                "one.bin",
            )


if __name__ == "__main__":
    unittest.main()

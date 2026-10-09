from __future__ import annotations

import hashlib
import json
import contextlib
import io
import tempfile
import threading
import unittest
import urllib.error
import zlib
from pathlib import Path
from unittest import mock

from meta_pcvr_downloader import download
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


def _manifest(segments):
    files = {}
    for index, clear in enumerate(segments):
        digest = hashlib.sha256(clear).hexdigest()
        files[f"{index}.bin"] = {
            "size": len(clear),
            "sha256": digest,
            "segments": [[0, digest]],
        }
    return {"files": files}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class SegmentFailureTests(unittest.TestCase):
    def test_dropped_connection_is_retried(self):
        clear = b"segment payload"
        attempts = []

        def urlopen(_request, timeout):
            attempts.append(timeout)
            if len(attempts) == 1:
                raise ConnectionResetError("connection reset by peer")
            return _Response(zlib.compress(clear))

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(download.urllib.request, "urlopen", urlopen),
        ):
            root = Path(directory)
            build = Build("1", "Test", "2", "1.0", 1)
            Downloader("token", build, root / "game", root / "cache", jobs=1).run(
                _manifest([clear])
            )
            self.assertEqual((root / "game/0.bin").read_bytes(), clear)
        self.assertEqual(len(attempts), 2)

    def test_failed_segment_stops_the_remaining_downloads(self):
        segments = [f"segment {index}".encode() for index in range(200)]
        requested = []
        lock = threading.Lock()

        def urlopen(request, timeout):
            with lock:
                requested.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(download.urllib.request, "urlopen", urlopen),
        ):
            root = Path(directory)
            build = Build("1", "Test", "2", "1.0", 1)
            with self.assertRaisesRegex(DownloadError, "HTTP 403"):
                Downloader("token", build, root / "game", root / "cache", jobs=4).run(
                    _manifest(segments)
                )
        # Only segments already in flight may start; the rest are cancelled.
        self.assertLess(len(requested), 20)


class AssemblyProgressTests(unittest.TestCase):
    def _run(self, root, manifest):
        output = io.StringIO()
        build = Build("1", "Test", "2", "1.0", 1)
        with (
            mock.patch.object(download, "PROGRESS_INTERVAL", 0),
            contextlib.redirect_stdout(output),
        ):
            Downloader("token", build, root / "game", root / "cache", jobs=1).run(
                manifest
            )
        return [line.strip() for line in output.getvalue().splitlines()]

    def test_large_file_reports_progress_while_it_is_written(self):
        parts = [b"a" * 10, b"b" * 10, b"c" * 10]
        hashes = [hashlib.sha256(part).hexdigest() for part in parts]
        whole = b"".join(parts)
        manifest = {
            "files": {
                "big.pak": {
                    "size": len(whole),
                    "sha256": hashlib.sha256(whole).hexdigest(),
                    "segments": [
                        [index * 10, value] for index, value in enumerate(hashes)
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cache").mkdir()
            for part, value in zip(parts, hashes):
                (root / "cache" / value).write_bytes(part)

            lines = self._run(root, manifest)
            files = [line for line in lines if line.startswith("files ")]
            # Mid-file reports keep 0 files done; the last one counts the file.
            self.assertTrue(files[0].startswith("files 0/1"))
            self.assertTrue(files[-1].startswith("files 1/1"))
            self.assertIn("Assembling and validating 1 files...", lines)

            # A resumed download with every file already valid still finishes
            # with a final progress line.
            resumed = self._run(root, manifest)
            self.assertTrue(resumed[-1].startswith("files 1/1"))


if __name__ == "__main__":
    unittest.main()

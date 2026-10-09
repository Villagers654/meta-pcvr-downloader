from __future__ import annotations

import concurrent.futures
import hashlib
import http.client
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

from .api import USER_AGENT, Build


class DownloadError(RuntimeError):
    pass


class _Cancelled(Exception):
    """Another segment already failed, so this one stops early."""


SEGMENT_ATTEMPTS = 5
PROGRESS_INTERVAL = 0.5


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_manifest(token: str, build: Build) -> dict:
    query = urllib.parse.urlencode(
        {"id": build.binary_id, "access_token": token, "get_manifest": "1"}
    )
    request = urllib.request.Request(
        f"https://securecdn.oculus.com/binaries/download/?{query}",
        headers={"User-Agent": USER_AGENT},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            break
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise DownloadError(
                    "Meta refused the manifest. Confirm this account owns the PCVR/Rift version "
                    "and refresh your login."
                ) from error
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise DownloadError(
                    f"Manifest request returned HTTP {error.code}"
                ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise DownloadError(
                    f"Could not download the manifest: {error}"
                ) from error
        time.sleep(2**attempt)
    try:
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            name = "manifest.json" if "manifest.json" in names else names[0]
            manifest = json.loads(archive.read(name))
    except (zipfile.BadZipFile, IndexError, json.JSONDecodeError) as error:
        raise DownloadError("Meta returned an invalid build manifest") from error
    if not isinstance(manifest.get("files"), dict):
        raise DownloadError("The manifest contains no file list")
    return manifest


def _safe_destination(root: Path, relative_name: str) -> Path:
    relative = Path(relative_name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise DownloadError(f"Unsafe path in manifest: {relative_name}")
    destination = root / relative
    resolved_root = root.resolve()
    resolved_parent = destination.parent.resolve(strict=False)
    if (
        resolved_parent != resolved_root
        and resolved_root not in resolved_parent.parents
    ):
        raise DownloadError(
            f"Manifest path escapes the output directory: {relative_name}"
        )
    return destination


class Downloader:
    def __init__(
        self, token: str, build: Build, output: Path, cache: Path, jobs: int = 8
    ):
        self.token = token
        self.build = build
        self.output = output.expanduser()
        self.cache = cache.expanduser()
        self.jobs = jobs
        self._stop = threading.Event()

    def _download_segment(self, segment_hash: str) -> tuple[str, int, bool]:
        destination = self.cache / segment_hash
        if destination.is_file() and _sha256_file(destination) == segment_hash:
            return segment_hash, destination.stat().st_size, True
        destination.unlink(missing_ok=True)
        partial = self.cache / f".{segment_hash}.{os.getpid()}.part"
        partial.unlink(missing_ok=True)
        query = urllib.parse.urlencode(
            {
                "access_token": self.token,
                "binary_id": self.build.binary_id,
                "segment_sha256": segment_hash,
            }
        )
        request = urllib.request.Request(
            f"https://securecdn.oculus.com/binaries/segment/?{query}",
            headers={"User-Agent": USER_AGENT},
        )
        digest = hashlib.sha256()
        size = 0
        inflater = zlib.decompressobj()
        for attempt in range(SEGMENT_ATTEMPTS):
            digest = hashlib.sha256()
            size = 0
            inflater = zlib.decompressobj()
            partial.unlink(missing_ok=True)
            try:
                with (
                    urllib.request.urlopen(request, timeout=120) as response,
                    partial.open("wb") as output,
                ):
                    while compressed := response.read(1024 * 1024):
                        if self._stop.is_set():
                            raise _Cancelled
                        clear = inflater.decompress(compressed)
                        output.write(clear)
                        digest.update(clear)
                        size += len(clear)
                    clear = inflater.flush()
                    output.write(clear)
                    digest.update(clear)
                    size += len(clear)
                break
            except urllib.error.HTTPError as error:
                partial.unlink(missing_ok=True)
                if (
                    error.code not in {429, 500, 502, 503, 504}
                    or attempt == SEGMENT_ATTEMPTS - 1
                ):
                    raise DownloadError(
                        f"Segment {segment_hash} returned HTTP {error.code}"
                    ) from error
            except _Cancelled:
                partial.unlink(missing_ok=True)
                raise
            # A connection dropped mid-transfer surfaces as ConnectionResetError,
            # IncompleteRead or an SSL error, not only URLError; all are retryable.
            except (OSError, http.client.HTTPException, zlib.error) as error:
                partial.unlink(missing_ok=True)
                if attempt == SEGMENT_ATTEMPTS - 1:
                    raise DownloadError(
                        f"Could not download segment {segment_hash}: "
                        f"{type(error).__name__}: {error}"
                    ) from error
            if self._stop.wait(min(2**attempt, 30)):
                raise _Cancelled
        if digest.hexdigest() != segment_hash:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"Hash mismatch for segment {segment_hash}")
        os.replace(partial, destination)
        return segment_hash, size, False

    def run(self, manifest: dict) -> None:
        files = manifest["files"]
        segment_hashes = sorted(
            {str(segment[1]) for item in files.values() for segment in item["segments"]}
        )
        self.output.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        total_size = sum(int(item["size"]) for item in files.values())
        free = shutil.disk_usage(self.output).free
        if free < total_size:
            raise DownloadError(
                f"Not enough free space: need {total_size / 2**30:.2f} GiB, "
                f"have {free / 2**30:.2f} GiB"
            )

        print(
            f"Preparing {len(segment_hashes)} unique segments with {self.jobs} workers..."
        )
        finished = cached = 0
        self._stop.clear()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs)
        try:
            futures = [
                executor.submit(self._download_segment, value)
                for value in segment_hashes
            ]
            for future in concurrent.futures.as_completed(futures):
                _, _, was_cached = future.result()
                finished += 1
                cached += int(was_cached)
                if finished % 50 == 0 or finished == len(futures):
                    print(
                        f"  segments {finished}/{len(futures)} ({cached} cached)",
                        flush=True,
                    )
        except BaseException:
            # One failed segment fails the download. Stop the others now
            # instead of fetching every remaining segment before reporting it.
            self._stop.set()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        print(f"Assembling and validating {len(files)} files...", flush=True)
        completed_bytes = 0
        # Games range from thousands of small files to a few 10+ GiB archives,
        # so report on a clock rather than every Nth file.
        last_report = time.monotonic()

        def report(done_files: int, done_bytes: int, *, final: bool = False) -> None:
            nonlocal last_report
            now = time.monotonic()
            if final or now - last_report >= PROGRESS_INTERVAL:
                last_report = now
                print(
                    f"  files {done_files}/{len(files)} "
                    f"({done_bytes / 2**30:.2f}/{total_size / 2**30:.2f} GiB)",
                    flush=True,
                )

        for index, (relative_name, item) in enumerate(files.items(), 1):
            destination = _safe_destination(self.output, relative_name)
            expected_size = int(item["size"])
            expected_hash = str(item["sha256"])
            if (
                destination.is_file()
                and destination.stat().st_size == expected_size
                and _sha256_file(destination) == expected_hash
            ):
                size = expected_size
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial = destination.with_name(
                    destination.name + f".{os.getpid()}.part"
                )
                digest = hashlib.sha256()
                size = 0
                try:
                    with partial.open("wb") as output:
                        for segment in item["segments"]:
                            with (self.cache / str(segment[1])).open("rb") as source:
                                for chunk in iter(
                                    lambda: source.read(4 * 1024 * 1024), b""
                                ):
                                    output.write(chunk)
                                    digest.update(chunk)
                                    size += len(chunk)
                                    report(index - 1, completed_bytes + size)
                    if size != expected_size or digest.hexdigest() != expected_hash:
                        raise DownloadError(
                            f"Validation failed while assembling {relative_name}"
                        )
                    os.replace(partial, destination)
                except Exception:
                    partial.unlink(missing_ok=True)
                    raise
            completed_bytes += size
            report(index, completed_bytes, final=index == len(files))

        manifest_path = self.output / "manifest.json"
        temporary = manifest_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(temporary, manifest_path)

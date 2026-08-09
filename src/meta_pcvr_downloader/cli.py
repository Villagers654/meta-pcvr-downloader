from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from . import __version__
from .api import MetaApiError, list_builds, parse_app_id, select_build
from .auth import AuthenticationError, get_access_token
from .download import DownloadError, Downloader, fetch_manifest


def _safe_folder_name(value: str) -> str:
    value = value.replace(":", " -")
    value = re.sub(r"[\\/*?\"<>|]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Meta PCVR Game"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-pcvr",
        description="Download Meta/Oculus PCVR games you own on Linux.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    versions = subparsers.add_parser("versions", help="list available PCVR builds")
    versions.add_argument("app", help="numeric app ID or Meta PCVR store URL")

    download = subparsers.add_parser("download", help="download and validate a PCVR build")
    download.add_argument("app", help="numeric app ID or Meta PCVR store URL")
    download.add_argument(
        "--build", help="version string, version code, or binary ID (default: newest)"
    )
    download.add_argument("--output", type=Path, help="destination game directory")
    download.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache/meta-pcvr-downloader/segments",
        help="content-addressed segment cache",
    )
    download.add_argument("--jobs", type=int, default=8, choices=range(1, 33), metavar="1-32")
    download.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    download.add_argument("--dry-run", action="store_true", help="show the selected build only")
    return parser


def run(arguments: argparse.Namespace) -> int:
    app_id = parse_app_id(arguments.app)
    print("Finding your Meta login...")
    token = get_access_token()
    builds = list_builds(token, app_id)
    if arguments.command == "versions":
        print(f"{builds[0].app_name} ({app_id})")
        for build in builds:
            print(f"  {build.version:<28} code={build.version_code:<8} binary={build.binary_id}")
        return 0

    build = select_build(builds, arguments.build)
    output = arguments.output or Path.home() / "Games/Meta PCVR" / _safe_folder_name(build.app_name)
    print(f"Game:    {build.app_name}")
    print(f"Build:   {build.version} (code {build.version_code}, binary {build.binary_id})")
    print(f"Output:  {output.expanduser()}")
    if arguments.dry_run:
        return 0
    if not arguments.yes:
        answer = input("Download this entitled PCVR build? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return 0
    print("Downloading build manifest...")
    manifest = fetch_manifest(token, build)
    Downloader(token, build, output, arguments.cache, arguments.jobs).run(manifest)
    print(f"Complete: {build.app_name} {build.version}")
    print(f"Launch file: {output.expanduser() / manifest.get('launchFile', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parser().parse_args(argv))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (AuthenticationError, DownloadError, MetaApiError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

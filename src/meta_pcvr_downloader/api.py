from __future__ import annotations

from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import time


USER_AGENT = "meta-pcvr-downloader/0.1"
GRAPHQL_URL = "https://graph.oculus.com/graphql"
VERSIONS_DOCUMENT_ID = "1586217024733717"


class MetaApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Build:
    app_id: str
    app_name: str
    binary_id: str
    version: str
    version_code: int
    change_log: str | None = None


def parse_app_id(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        return value
    match = re.search(r"(?:^|/)(\d{8,})(?:/|$|[?#])", value)
    if match:
        return match.group(1)
    raise ValueError("Expected a numeric Meta app ID or a Meta store URL containing one")


def _post_graphql(token: str, document_id: str, variables: dict[str, object]) -> dict:
    body = urllib.parse.urlencode(
        {
            "access_token": token,
            "variables": json.dumps(variables, separators=(",", ":")),
            "doc_id": document_id,
        }
    ).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                message = error.read().decode(errors="replace")[:500]
                raise MetaApiError(f"Meta API returned HTTP {error.code}: {message}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 2:
                raise MetaApiError(f"Could not reach the Meta API: {error}") from error
        time.sleep(2**attempt)
    else:
        raise MetaApiError(f"Could not reach the Meta API: {last_error}")
    if payload.get("errors"):
        raise MetaApiError(f"Meta API error: {payload['errors'][0].get('message', payload['errors'][0])}")
    return payload


def list_builds(token: str, app_id: str) -> list[Build]:
    payload = _post_graphql(token, VERSIONS_DOCUMENT_ID, {"id": app_id})
    try:
        node = payload["data"]["node"]
        name = node.get("displayName") or node.get("display_name") or app_id
        edges = node["supportedBinaries"]["edges"]
    except (KeyError, TypeError) as error:
        raise MetaApiError("Meta returned no PCVR application data for that app ID") from error
    builds = []
    for edge in edges:
        binary = edge.get("node") or {}
        if binary.get("__typename") != "PCBinary":
            continue
        builds.append(
            Build(
                app_id=app_id,
                app_name=name,
                binary_id=str(binary["id"]),
                version=str(binary.get("version", "unknown")),
                version_code=int(binary.get("versionCode", 0)),
                change_log=binary.get("changeLog"),
            )
        )
    if not builds:
        raise MetaApiError("No Rift/PCVR builds were returned for this app")
    return sorted(builds, key=lambda build: build.version_code, reverse=True)


def select_build(builds: list[Build], selector: str | None) -> Build:
    if selector is None:
        return builds[0]
    for build in builds:
        if selector in {build.binary_id, build.version, str(build.version_code)}:
            return build
    choices = ", ".join(f"{item.version} ({item.version_code})" for item in builds)
    raise MetaApiError(f"Build {selector!r} was not found. Available builds: {choices}")

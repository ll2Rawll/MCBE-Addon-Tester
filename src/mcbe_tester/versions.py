"""Query official current download metadata, without guessing client package version equivalence."""

import json
import re
import urllib.request
from urllib.parse import urlparse

from .util import utc_now

SOURCE = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"


def latest_builds():
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "MCBE-Addon-Tester/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Unexpectedly large release metadata response")
    payload = json.loads(raw)
    builds = {}
    for link in payload["result"]["links"]:
        kind = link.get("downloadType", "")
        if not kind.startswith("serverBedrock"):
            continue
        url = link["downloadUrl"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"minecraft.net", "www.minecraft.net"}:
            raise ValueError("Unexpected download host in official release metadata")
        match = re.search(r"bedrock-server-(\d+(?:\.\d+){3})\.zip$", parsed.path)
        if not match:
            raise ValueError("Release metadata format changed; do not guess a build")
        builds[kind] = {"build": match[1], "download_url": url}
    if not builds:
        raise ValueError("No Bedrock builds found in official metadata")
    return {
        "source": SOURCE,
        "checked_at": utc_now(),
        "builds": builds,
        "note": "Official BDS releases. Windows client file versions use different formatting; "
        "verify the installed retail client/update channel separately. No game compatibility test performed.",
    }

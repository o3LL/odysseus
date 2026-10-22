"""Offline incident-chain checks and loopback boot smoke for the derived image."""

import hashlib
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

import certifi

from repair_trust import check_bundle


def check_chains(baseline=False):
    incident = json.loads(Path(__file__).with_name("incident.json").read_text())
    system = Path(os.environ["SSL_CERT_FILE"])
    capath = os.environ["SSL_CERT_DIR"]
    stores = [["-CAfile", str(system), "-no-CApath"],
              ["-CAfile", certifi.where(), "-no-CApath"],
              ["-no-CAfile", "-CApath", capath]]
    if not baseline:
        check_bundle(system)
        check_bundle(Path(certifi.where()))
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for fingerprint, pem in incident["certificates"].items():
            assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() == fingerprint
            (directory / fingerprint).write_text(pem)
        positive = directory / "affected.pem"
        positive.write_text("\n".join(incident["certificates"][fp] for fp in incident["roots"]))
        for hostname, chain in incident["chains"].items():
            untrusted = directory / "untrusted.pem"
            untrusted.write_text("\n".join(incident["certificates"][fp] for fp in chain[1:]))
            base = ["openssl", "verify", "-no-CAstore", "-attime", str(incident["verification_time"]),
                    "-purpose", "sslserver", "-verify_hostname", hostname,
                    "-x509_strict", "-partial_chain", "-trusted_first", "-untrusted", str(untrusted)]
            expectations = [(s, baseline and (index != 1 or "-aaa-" not in hostname))
                            for index, s in enumerate(stores)]
            for selection, expected in expectations + [
                    (["-CAfile", str(positive), "-no-CApath"], True)]:
                result = subprocess.run(base + selection + [str(directory / chain[0])],
                                        capture_output=True, text=True)
                if (result.returncode == 0) != expected:
                    raise RuntimeError(f"unexpected chain result: {hostname}: {selection}: {result.stderr}")
    if baseline:
        print("Affected base reproduced: system PEM and directory accept 12/12; certifi accepts 6/12")
    else:
        print("12/12 incident chains rejected by system PEM, hashed directory and certifi; affected controls accepted")


def check_boot(live=False):
    with tempfile.TemporaryDirectory() as directory:
        settings = Path(directory) / "settings.yml"
        settings.write_text("use_default_settings: true\nserver:\n  secret_key: offline-build-validation-only\n  limiter: false\nsearch:\n  formats: [html, json]\n")
        cache = Path(directory) / "cache"
        cache.mkdir()
        env = dict(os.environ, SEARXNG_SETTINGS_PATH=str(settings),
                   __SEARXNG_CONFIG_PATH=directory, __SEARXNG_DATA_PATH=str(cache),
                   GRANIAN_HOST="127.0.0.1", GRANIAN_PORT="18888")
        # Do not expose a port or issue an outbound search during offline builds.
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(["/usr/local/searxng/entrypoint.sh"],
                                       cwd="/usr/local/searxng", env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    if process.poll() is not None:
                        break
                    try:
                        with opener.open("http://127.0.0.1:18888/healthz", timeout=1) as response:
                            if response.status == 200:
                                print("SearXNG loopback healthcheck passed")
                                # Its root entrypoint regenerates system trust.
                                # Recheck after that real startup path completes.
                                check_bundle(Path(os.environ["SSL_CERT_FILE"]))
                                for path in Path(os.environ["SSL_CERT_DIR"]).iterdir():
                                    if re.fullmatch(r"[0-9a-fA-F]{8}\.[0-9]+", path.name):
                                        check_bundle(path)
                                if live:
                                    with opener.open("http://127.0.0.1:18888/search?q=python+documentation&format=json", timeout=90) as search:
                                        payload = json.load(search)
                                    results = payload.get("results", [])
                                    if not results:
                                        raise RuntimeError("live search returned no results")
                                    print(f"SearXNG live JSON search passed: {len(results)} results")
                                return
                    except OSError:
                        time.sleep(0.5)
                log.seek(0)
                raise RuntimeError("SearXNG failed boot smoke: " + log.read().decode(errors="replace")[-6000:])
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


async def check_https():
    # Exercise the actual pinned SearXNG client factory with its default CA
    # selection, rather than a different HTTP library's trust configuration.
    sys.path.insert(0, "/usr/local/searxng")
    from searx.network.client import new_client

    client = new_client(enable_http=False, verify=True, enable_http2=True,
                        enable_http3=False, max_connections=2, proxies={},
                        local_address=None, max_redirects=3)
    try:
        response = await client.get("https://pypi.org/simple/certifi/", timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"ordinary HTTPS failed: {response.status_code}")
        print("SearXNG actual curl client ordinary HTTPS passed with verification enabled")
    finally:
        await client.aclose()


if __name__ == "__main__":
    baseline = "--baseline" in sys.argv
    check_chains(baseline=baseline)
    live = "--live" in sys.argv
    if live:
        asyncio.run(check_https())
    if not baseline:
        check_boot(live=live)

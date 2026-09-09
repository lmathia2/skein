import hashlib
import json
from pathlib import Path


def test_vendored_sources_match_upstream_hashes():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "harness/_vendor/SOURCES.json").read_text())
    for path, expected in manifest.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == expected, path

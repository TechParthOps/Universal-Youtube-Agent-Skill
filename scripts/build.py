#!/usr/bin/env python3
"""Build local sdist/wheel artifacts with a SHA-256 manifest (no upload).

    python scripts/build.py [--out dist]

Requires the optional build extra:  pip install 'universal-creator-agent[build]'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from creator_core.release.build import build_artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist", help="output directory inside the checkout")
    parser.add_argument("--json", action="store_true", help="emit a JSON result")
    args = parser.parse_args(argv)
    result = build_artifacts(REPO_ROOT, args.out)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Built {len(result['artifacts'])} artifact(s) in {result['out']}")
        for item in result["artifacts"]:
            print(f"  {item['name']}  sha256={item['sha256'][:16]}...")
        print(f"  manifest: {result['manifest']}")
        print("  " + result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

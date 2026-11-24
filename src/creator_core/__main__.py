"""`python -m creator_core` — same entry point as the installed `creator` script."""

import sys

from creator_core.cli.main import main

if __name__ == "__main__":
    sys.exit(main())

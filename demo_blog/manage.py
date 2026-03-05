#!/usr/bin/env python3
"""Zeeb project management script."""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    from zeeb_orm.cli.main import main
    sys.exit(main())

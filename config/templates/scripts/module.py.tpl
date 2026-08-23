"""
{{DESCRIPTION}}
"""

import sys
from pathlib import Path


def main() -> int:
    """Точка входа."""
    print(f"Работает {Path(__file__).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

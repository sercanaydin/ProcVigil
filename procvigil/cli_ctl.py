"""'pvctl' konsol komutu — 'procvigil ctl ...' için kısayol.

Örn:  pvctl status   ==  python -m procvigil ctl status
"""

from __future__ import annotations

import sys

from .__main__ import main as _main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _main(["ctl", *args])


if __name__ == "__main__":
    raise SystemExit(main())

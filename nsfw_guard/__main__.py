"""Entry point.

    python -m nsfw_guard            # desktop UI (pywebview)
    python -m nsfw_guard --cli ...  # terminal interface
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--cli" in argv:
        argv.remove("--cli")
        from .cli import main as cli_main
        return cli_main(argv)
    from .server import main as gui_main
    return gui_main(argv)


if __name__ == "__main__":
    sys.exit(main())

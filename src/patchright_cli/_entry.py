"""Console-script entry point with a `--version` fast path.

Agents probe `--version` constantly, so a bare version flag is answered here
from the leaf `patchright_cli/__init__.py`, before cli.py pulls in click and
the daemon (and with it Patchright). Everything else goes to cli.main().
"""

import sys

_VERSION_FLAGS = ("-v", "-V", "--version")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in _VERSION_FLAGS:
        from patchright_cli import __version__

        sys.stdout.write(__version__ + "\n")
        return

    from patchright_cli.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()

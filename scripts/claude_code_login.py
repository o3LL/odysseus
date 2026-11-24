#!/usr/bin/env python3
"""Sign an Odysseus user in to their own Claude Code, in a terminal on the server.

Runs Claude Code's own ``claude auth login`` with that user's private
``CLAUDE_CONFIG_DIR`` (under ``DATA_DIR/claude_code``), so the sign-in goes
through Anthropic's flow and the credential stays in Claude Code's own files.
Odysseus never sees it. Pick a Claude subscription or a Console API key in that
flow; both work for Claude Code runs.

Usage:
    python scripts/claude_code_login.py --user alice          # sign in
    python scripts/claude_code_login.py --user alice status   # check
    python scripts/claude_code_login.py --user alice logout
    docker compose exec -it odysseus python scripts/claude_code_login.py --user alice

Omit --user when auth is disabled (single-user mode).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import claude_code_runner as runner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", default=None, help="Odysseus username (omit in single-user mode)")
    parser.add_argument("action", nargs="?", default="login", choices=("login", "status", "logout"))
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="extra arguments for `claude auth <action>`")
    args = parser.parse_args()

    cli = runner.find_cli()
    if not cli:
        print(runner.NOT_INSTALLED, file=sys.stderr)
        return 1
    home = runner.user_home(args.user)
    print(f"Claude Code config for {args.user or 'single-user mode'}: {home.config_dir}", file=sys.stderr)
    os.chdir(home.workspace)
    os.execve(cli, [cli, "auth", args.action, *args.extra], runner.build_env(home))
    return 0  # pragma: no cover - execve does not return


if __name__ == "__main__":
    sys.exit(main())

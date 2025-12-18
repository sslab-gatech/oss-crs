"""Utility functions for bug_finding package."""

import subprocess
from typing import List

# Global gitcache setting
USE_GITCACHE = False


def set_gitcache(enabled: bool):
    """Set global gitcache mode."""
    global USE_GITCACHE
    USE_GITCACHE = enabled


def run_git(args: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Run git command, optionally with gitcache prefix."""
    if USE_GITCACHE:
        cmd = f"gitcache git {' '.join(args)}"
        return subprocess.run(cmd, shell=True, check=True, **kwargs)
    else:
        return subprocess.run(['git'] + args, check=True, **kwargs)

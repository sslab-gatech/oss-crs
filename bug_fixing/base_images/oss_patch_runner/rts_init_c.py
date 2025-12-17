#!/usr/bin/env python3
"""
RTS initialization script for C/C++ projects.

Installs dependencies needed for BinaryRTS:
- System packages for building ctags and poetry
- Universal-ctags from source (for function parsing with macrodef support)
- Poetry (for binary-rts CLI)
"""

import subprocess
import sys

CTAGS_REPO = "https://github.com/universal-ctags/ctags.git"
BINARY_RTS_REPO = "https://github.com/Team-Atlanta/binary-rts.git"
PIN_URL = "https://software.intel.com/sites/landingpage/pintool/downloads/pin-external-4.0-99633-g5ca9893f2-gcc-linux.tar.gz"


def run(cmd: list[str] | str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command and print it."""
    if isinstance(cmd, list):
        print(f"+ {' '.join(cmd)}", flush=True)
    else:
        print(f"+ {cmd}", flush=True)
    return subprocess.run(cmd, check=check, **kwargs)


def install_apt_packages():
    """Install system packages needed for ctags build and poetry."""
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y",
         "autoconf", "automake", "pkg-config", "libjansson-dev", "libyaml-dev", "python3-pip"])


def build_ctags(ctags_dir: str = "/tmp/ctags"):
    """Build universal-ctags from source for macrodef field and JSON support."""
    run(["rm", "-rf", ctags_dir])
    run(["git", "clone", "--depth=1", CTAGS_REPO, ctags_dir])
    run(["./autogen.sh"], cwd=ctags_dir)
    run(["./configure"], cwd=ctags_dir)
    run("make -j$(nproc)", cwd=ctags_dir, shell=True)
    run(["make", "install"], cwd=ctags_dir)
    run(["rm", "-rf", ctags_dir])


def install_poetry():
    """Install poetry via pip."""
    run([sys.executable, "-m", "pip", "install", "poetry"])


def clone_binary_rts(install_dir: str = "/opt/binary-rts"):
    """Clone binary-rts repo (includes pintools-rts) and install CLI dependencies."""
    run(["rm", "-rf", install_dir])
    run(["git", "clone", "--depth=1", BINARY_RTS_REPO, install_dir])
    run(["poetry", "install", "--no-interaction"], cwd=f"{install_dir}/binaryrts/cli")


def build_pintools_rts(binary_rts_dir: str = "/opt/binary-rts", pin_root: str = "/opt/pin"):
    """Build pintools-rts Pin tool and listener library."""
    pintools_dir = f"{binary_rts_dir}/pintools-rts"
    env = {**dict(__import__("os").environ), "PIN_ROOT": pin_root}
    run("make -j$(nproc)", cwd=pintools_dir, shell=True, env=env)
    run("make listener", cwd=pintools_dir, shell=True)


def install_pin(install_dir: str = "/opt/pin"):
    """Download and extract Intel Pin."""
    tarball = "/tmp/pin.tar.gz"
    run(["curl", "-L", "-o", tarball, PIN_URL])
    run(["rm", "-rf", install_dir])
    run(["mkdir", "-p", install_dir])
    run(["tar", "-xzf", tarball, "-C", install_dir, "--strip-components=1"])
    run(["rm", "-f", tarball])


def main():
    print("=== Installing apt packages ===", flush=True)
    install_apt_packages()

    print("=== Building universal-ctags ===", flush=True)
    build_ctags()

    print("=== Installing poetry ===", flush=True)
    install_poetry()

    print("=== Installing Intel Pin ===", flush=True)
    install_pin()

    print("=== Cloning binary-rts ===", flush=True)
    clone_binary_rts()

    print("=== Building pintools-rts ===", flush=True)
    build_pintools_rts()

    print("=== RTS C/C++ initialization complete ===", flush=True)


if __name__ == "__main__":
    main()

"""Install the native apple-stt-helper: download a release binary or compile it."""
import argparse
import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from ovos_stt_apple.version import __version__

LOG = logging.getLogger("ovos_stt_apple.build")

GITHUB_REPO = "timonvanhasselt/ovos-stt-plugin-apple"
RELEASE_URL = "https://github.com/{repo}/releases/download/v{version}/{name}"

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFT_DIR = os.path.join(HERE, "swift")


def default_helper_path() -> str:
    return os.path.expanduser(
        "~/Library/Application Support/ovos_stt_apple/apple-stt-helper")


def _require_macos():
    if platform.system() != "Darwin":
        raise RuntimeError("apple-stt-helper can only be installed on macOS")


def asset_name() -> str:
    """Release asset for this machine, e.g. apple-stt-helper-arm64."""
    arch = {"arm64": "arm64", "aarch64": "arm64",
            "x86_64": "x86_64", "amd64": "x86_64"}.get(platform.machine().lower())
    if arch is None:
        raise RuntimeError(f"unsupported architecture: {platform.machine()}")
    return f"apple-stt-helper-{arch}"


def _fetch(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ovos-stt-plugin-apple"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download_helper(dest: str = None, version: str = None, repo: str = None) -> str:
    """Download the prebuilt binary from a GitHub release and verify its SHA256."""
    _require_macos()
    dest = os.path.expanduser(dest or default_helper_path())
    version = version or __version__
    repo = repo or GITHUB_REPO
    name = asset_name()

    sums = _fetch(RELEASE_URL.format(repo=repo, version=version, name="SHA256SUMS")).decode()
    expected = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            expected = parts[0].lower()
    if not expected:
        raise RuntimeError(f"{name} not listed in SHA256SUMS")

    data = _fetch(RELEASE_URL.format(repo=repo, version=version, name=name), timeout=120)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {name}: expected {expected}, got {actual}")

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest)
    # Not normally set for urllib downloads, but harmless if it is.
    subprocess.run(["xattr", "-d", "com.apple.quarantine", dest],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dest


def build_helper(dest: str = None, force: bool = False) -> str:
    """Compile the bundled Swift source (needs Xcode command line tools)."""
    _require_macos()
    if not shutil.which("swiftc"):
        raise RuntimeError("swiftc not found; run: xcode-select --install")
    dest = os.path.expanduser(dest or default_helper_path())
    if os.path.exists(dest) and not force:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(
        ["swiftc", "-O", os.path.join(SWIFT_DIR, "main.swift"), "-o", dest,
         "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
         "-Xlinker", "__info_plist",
         "-Xlinker", os.path.join(SWIFT_DIR, "Info.plist")],
        check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", dest], check=True)
    return dest


def ensure_helper(dest: str = None, force: bool = False, mode: str = "auto") -> str:
    """mode: 'auto' (download, then compile), 'download' or 'compile'."""
    dest = os.path.expanduser(dest or default_helper_path())
    if os.path.exists(dest) and not force:
        return dest
    if mode == "download":
        return download_helper(dest)
    if mode == "compile":
        return build_helper(dest, force=True)
    try:
        return download_helper(dest)
    except Exception as e:  # noqa
        LOG.warning(f"download failed ({e}); trying to compile locally")
    try:
        return build_helper(dest, force=True)
    except Exception as e:  # noqa
        raise RuntimeError(
            f"could not install apple-stt-helper (download and compile both failed: {e}). "
            "Install Xcode command line tools (xcode-select --install) or check your "
            "internet connection.")


def main():
    p = argparse.ArgumentParser(description="Install apple-stt-helper")
    p.add_argument("--dest", help="output path")
    p.add_argument("--force", action="store_true", help="replace an existing helper")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--download", action="store_true", help="only download the release binary")
    g.add_argument("--compile", action="store_true", help="only compile from source")
    a = p.parse_args()
    mode = "download" if a.download else "compile" if a.compile else "auto"
    try:
        print(ensure_helper(a.dest, a.force, mode))
    except Exception as e:  # noqa
        print(f"failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

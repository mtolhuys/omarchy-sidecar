#!/usr/bin/python3

"""Release-blocking supply-chain and artifact assertions."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


PROJECT = Path(__file__).resolve().parents[1]
ARTIFACT = PROJECT / "dist" / "omarchy-sidecar-0.2.1.tar.gz"
CHECKSUM = ARTIFACT.with_name(ARTIFACT.name + ".sha256")
RUNTIME_DIRS = ("sidecar", "helper", "service", "bar-widget", "web")
ARTIFACT_TOP_LEVEL = {
    "manifest.json", "service", "bar-widget", "helper", "sidecar", "web", "docs", "preview.png",
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
    "PRIVACY.md", "SUPPORT.md",
}
FORBIDDEN_EXECUTABLE = re.compile(
    r"[\"'](?:sudo|su|pkexec|pacman|yay|paru|makepkg|systemctl|sshd?|ufw|firewall-cmd|iptables|nft)[\"']",
    re.IGNORECASE,
)
FORBIDDEN_TAILSCALE = re.compile(
    r"[\"']tailscale[\"']\s*,\s*[\"'](?:up|login|ssh|funnel|down|logout|set|switch|configure|cert)[\"']",
    re.IGNORECASE,
)
FORBIDDEN_FILENAMES = re.compile(
    r"(?:^|/)(?:PKGBUILD|\.INSTALL|INSTALL|.*\.install|.*\.service|.*\.timer|sudoers|sshd?_config)$",
    re.IGNORECASE,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_manifest(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    assert set(manifest) == {
        "schemaVersion", "id", "name", "version", "author", "description",
        "kinds", "entryPoints", "barWidget",
    }, "manifest gained an install/dependency hook"
    assert manifest["kinds"] == ["service", "bar-widget"]
    assert manifest["version"] == "0.2.1", "release version drifted"
    assert manifest["author"] == "Maarten Tolhuijs", "maintainer identity drifted"


def assert_build_path(root: Path) -> None:
    makefile = (root / "Makefile").read_text()
    assert not FORBIDDEN_EXECUTABLE.search(makefile), "privileged executable in build path"
    assert not re.search(r"\b(?:curl|wget|pip|npm\s+install|git\s+clone)\b", makefile), "network/dependency install in build path"
    assert "--owner=0 --group=0 --numeric-owner" in makefile
    assert "--sort=name --mtime=@0" in makefile
    local_update = root / "scripts" / "local-update"
    if local_update.exists():
        text = local_update.read_text()
        assert "refusing to install a desktop plugin as root" in text
        assert not FORBIDDEN_EXECUTABLE.search(text), "privileged executable in local update path"
        assert not FORBIDDEN_TAILSCALE.search(text), "forbidden Tailscale operation in local update path"
        assert "shell=True" not in text and "os.system" not in text


def assert_runtime_source(root: Path) -> None:
    assert_manifest(root)
    if (root / "Makefile").exists():
        assert_build_path(root)
    for directory in RUNTIME_DIRS:
        for path in (root / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            assert not FORBIDDEN_FILENAMES.search(relative), f"forbidden runtime path: {relative}"
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode & 0o6000 == 0, f"privileged mode bit on {relative}"
            if path.suffix in {".png", ".pyc"}:
                continue
            text = path.read_text(errors="strict")
            graph_ids = set(re.findall(r"v\d{4}", relative + "\n" + text))
            assert graph_ids <= {"v1012"}, f"stale runtime graph in {relative}: {sorted(graph_ids)}"
            assert not FORBIDDEN_EXECUTABLE.search(text), f"privileged executable primitive in {relative}"
            assert not FORBIDDEN_TAILSCALE.search(text), f"forbidden Tailscale operation in {relative}"
            assert "omarchy-install-service-tailscale" not in text, f"privileged installer trigger in {relative}"
            assert not re.search(r"\bomarchy(?:-|\s+)pkg(?:-|\s+)", text), f"package-manager trigger in {relative}"
            assert "shell=True" not in text and "shell = True" not in text, f"shell execution in {relative}"
            if path.suffix == ".py":
                tree = ast.parse(text, filename=relative)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        for keyword in node.keywords:
                            if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                                raise AssertionError(f"shell subprocess in {relative}")
    routes = (root / "sidecar" / "routes.py").read_text()
    assert routes.count('["tailscale", "status", "--json"]') == 1
    # Preflight, activation proof, and periodic health all use the same exact
    # read-only Serve-status primitive. Any command-surface change is a release
    # blocker and must update this explicit count after security review.
    assert routes.count('["tailscale", "serve", "status", "--json"]') == 3
    assert 'self._resolve_tailscale(), "serve", "--yes"' in routes
    assert "subprocess.run(command" in routes
    assert "os.path.realpath(resolved)" in routes
    assert "tailscale up" not in routes.lower()
    control = (root / "sidecar" / "control.py").read_text()
    assert 'operation == "command"' not in control and 'operation == "exec"' not in control
    for retired in (root / "sidecar" / "carry.py", root / "helper" / "sidecar-agent-setup", root / "codex"):
        assert not retired.exists(), f"retired Agent integration remains: {retired.relative_to(root)}"
    for relative in (
        "sidecar/app.py", "sidecar/core.py", "sidecar/server.py",
        "service/v1012/Service.qml", "bar-widget/v1012/BarWidget.qml",
        "web/dist/index.html", "web/dist/app.v1012.js", "web/dist/model.v1012.js", "web/dist/app.v1012.css",
    ):
        text = (root / relative).read_text()
        assert not re.search(r"\b(?:carry|codex)\b", text, re.IGNORECASE), f"retired feature remains in {relative}"
    inbox = (root / "sidecar" / "inbox.py").read_text()
    assert "shell=True" not in inbox and "os.system" not in inbox
    assert "MAX_INBOX_FILE_BYTES" in inbox and "MAX_INBOX_BATCH_BYTES" in inbox
    assert "INBOX_CHUNK_BYTES" in inbox and "read(min(INBOX_CHUNK_BYTES" in inbox
    assert "src_dir_fd=staging_fd" in inbox and "dst_dir_fd=inbox_fd" in inbox, "final commit must use verified directory descriptors"
    assert "os.O_NOFOLLOW" in inbox and "os.fstat(staged_read_fd)" in inbox
    assert "os.chmod(target_name, 0o600, dir_fd=inbox_fd, follow_symlinks=False)" in inbox
    assert 'self.inbox = self.downloads / "Sidecar"' in inbox, "destination escaped the fixed inbox"
    assert 'self.staging = self.inbox / ".sidecar-staging"' in inbox
    constants = (root / "sidecar" / "constants.py").read_text()
    for relative in ("sidecar/constants.py", "sidecar/core.py", "sidecar/adapters.py", "docs/PROTOCOL.md"):
        assert "media.setVolume" not in (root / relative).read_text(), f"undeclared volume action remains in {relative}"
    assert "pactl" not in (root / "sidecar" / "adapters.py").read_text(), "audio-volume command surface remains"
    for bound in ("MAX_INBOX_FILES = 5", "MAX_INBOX_FILE_BYTES = 25 * 1024 * 1024", "MAX_INBOX_BATCH_BYTES = 50 * 1024 * 1024"):
        assert bound in constants, f"Drop bound drifted: {bound}"
    worker = (root / "web" / "dist" / "sw.v1012.js").read_text()
    assert 'const CACHE = "sidecar-web-v1012-final"' in worker
    assert 'credential' not in worker[worker.index("async function writeShare"):worker.index("async function cleanupShares")], "credential entered share persistence"
    web_manifest = json.loads((root / "web" / "dist" / "manifest.webmanifest").read_text())
    assert web_manifest["share_target"]["method"] == "POST"
    assert web_manifest["share_target"]["enctype"] == "multipart/form-data"


def assert_artifact() -> None:
    expected = CHECKSUM.read_text().split()[0]
    assert expected == digest(ARTIFACT), "checksum does not match artifact"
    with tarfile.open(ARTIFACT, "r:gz") as archive:
        members = archive.getmembers()
        assert members, "empty artifact"
        for member in members:
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and ".." not in path.parts, f"unsafe artifact path: {member.name}"
            assert path.parts[0] == "omarchy-sidecar-0.2.1", f"unexpected artifact root: {member.name}"
            if len(path.parts) > 1:
                assert path.parts[1] in ARTIFACT_TOP_LEVEL, f"unexpected artifact top-level path: {member.name}"
            assert not FORBIDDEN_FILENAMES.search(member.name), f"forbidden artifact path: {member.name}"
            assert member.uid == 0 and member.gid == 0, f"non-reproducible ownership: {member.name}"
            assert member.mode & 0o6000 == 0, f"privileged artifact mode: {member.name}"
            assert member.isfile() or member.isdir(), f"unexpected artifact entry type: {member.name}"
            if member.isfile() and member.mode & 0o111:
                executable = "/".join(path.parts[1:])
                assert executable in {"helper/sidecard", "helper/sidecarctl"}, (
                    f"unexpected executable artifact file: {member.name}"
                )
            assert "__pycache__" not in path.parts and path.suffix != ".pyc"
        with tempfile.TemporaryDirectory() as temporary:
            archive.extractall(temporary, filter="data")
            assert_runtime_source(Path(temporary) / "omarchy-sidecar-0.2.1")


def main() -> None:
    assert_runtime_source(PROJECT)
    first = ARTIFACT.read_bytes()
    subprocess.run(["make", "dist"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)
    assert first == ARTIFACT.read_bytes(), "two consecutive distribution builds differ"
    assert_artifact()
    print("ok - forbidden primitives absent; non-privileged reproducible artifact inspected")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Rebuild exact approved lifecycle packages without credentials.

Node CI fetches this file from the pinned JOB_WORKFLOW_SHA, checks its
SHA-256, and executes ``python3 -I ci_secretless_rebuild_packages.py run``.
The workflow no longer inlines this executor.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    """Admit only the explicit workflow command before the rebuild runs."""
    if argv is None:
        argv = sys.argv[1:]
    if argv != ["run"]:
        raise SystemExit("usage: ci_secretless_rebuild_packages.py run")


if __name__ != "__main__":
    raise SystemExit(
        "ci_secretless_rebuild_packages.py is an executable lane, not a library"
    )
main()

import json
import errno
import os
import re
import shutil
import stat
import sys
from pathlib import Path

requested = [line.strip() for line in os.environ["REBUILD_PACKAGES"].splitlines() if line.strip()]
package_pattern = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")
skipped = [line.strip() for line in os.environ.get("SKIP_INSTALL_SCRIPTS", "").splitlines() if line.strip()]
if not requested and not skipped:
    sys.exit("at least one of secretless-rebuild-packages or secretless-skip-install-scripts is required")
if len(requested) != len(set(requested)) \
        or any(package_pattern.fullmatch(name) is None for name in requested):
    sys.exit("secretless-rebuild-packages accepts unique exact npm package names only")
if len(skipped) != len(set(skipped)) \
        or any(package_pattern.fullmatch(name) is None for name in skipped):
    sys.exit("secretless-skip-install-scripts accepts unique exact npm package names only")
overlap = sorted(set(requested) & set(skipped))
if overlap:
    sys.exit(
        "packages named in both secretless-rebuild-packages and "
        "secretless-skip-install-scripts: " + ", ".join(overlap)
    )
raw_rebuild_env = os.environ.get("REBUILD_ENV", "{}")
if len(raw_rebuild_env) > 4096:
    sys.exit("secretless-rebuild-env must not exceed 4096 characters")
try:
    rebuild_env = json.loads(raw_rebuild_env)
except json.JSONDecodeError as error:
    sys.exit(f"secretless-rebuild-env must be valid JSON: {error}")
if not isinstance(rebuild_env, dict) or len(rebuild_env) > 1:
    sys.exit("secretless-rebuild-env must be an object with at most one approved entry")
for name, value in rebuild_env.items():
    if name != "ONNXRUNTIME_NODE_INSTALL" or value != "skip":
        sys.exit("secretless-rebuild-env only accepts ONNXRUNTIME_NODE_INSTALL=skip")
if rebuild_env and "onnxruntime-node" not in requested:
    sys.exit("ONNXRUNTIME_NODE_INSTALL=skip requires onnxruntime-node in secretless-rebuild-packages")
sandbox_directory_name = "verjson-secretless-workspace"
package_manager = os.environ.get("PACKAGE_MANAGER", "npm")
if package_manager == "npm":
    lock = json.loads(Path("package-lock.json").read_text(encoding="utf-8"))
    installed = set()
    lifecycle_scoped = set()
    for path, package in lock.get("packages", {}).items():
        if "node_modules/" not in path:
            continue
        path_name = path.rsplit("node_modules/", 1)[1]
        locked_name = package.get("name")
        resolved_name = locked_name if isinstance(locked_name, str) else path_name
        installed.add(resolved_name)
        if package.get("hasInstallScript") is True:
            lifecycle_scoped.add(resolved_name)
    lock_nodes = []
    root_selectors = set()
    if skipped:
        # Every node npm rebuild can select, as (selector name, canonical
        # name, bundling ancestors). A link or workspace source directory is
        # selected by its directory name, not by its manifest name.
        lock_nodes = []
        lock_packages = lock.get("packages", {})
        def directory_selector(directory):
            parts = directory.rstrip("/").split("/")
            return "/".join(parts[-2:]) if len(parts) > 1 and parts[-2].startswith("@") else parts[-1]
        # The sandbox root is also selectable by its directory name, and by
        # its manifest name; neither may be requested next to a skip list.
        link_names_by_target = {}
        for link_path, link_package in lock_packages.items():
            if isinstance(link_package, dict) and link_package.get("link") is True \
                    and isinstance(link_package.get("resolved"), str):
                link_names_by_target.setdefault(
                    link_package["resolved"].rstrip("/"), []
                ).append(link_path.split("node_modules/")[-1])
        root_selectors = {sandbox_directory_name}
        root_name = lock_packages.get("", {}).get("name")
        if isinstance(root_name, str):
            root_selectors.add(root_name)
        for path, package in lock_packages.items():
            if path == "" or not isinstance(package, dict):
                continue
            segments = path.split("node_modules/")
            # Hosts keep their scope ("@scope/name"); a workspace host outside
            # node_modules is selected by its directory name.
            # A link to the host directory selects the host by the link's name too.
            ancestors = tuple(
                ([directory_selector(segments[0])]
                 + link_names_by_target.get(segments[0].rstrip("/"), [])) * bool(segments[0])
                + [segment.rstrip("/") for segment in segments[1:-1]]
            ) if package.get("inBundle") is True else ()
            if package.get("link") is True and isinstance(package.get("resolved"), str):
                target = lock_packages.get(package["resolved"], {})
                target_directory = directory_selector(package["resolved"])
                canonical_name = target.get("name") if isinstance(target.get("name"), str) else target_directory
                lock_nodes.append((segments[-1], canonical_name, ancestors, path))
                lock_nodes.append((target_directory, canonical_name, ancestors, package["resolved"]))
            elif "node_modules/" in path:
                canonical_name = package.get("name") if isinstance(package.get("name"), str) else segments[-1]
                lock_nodes.append((segments[-1], canonical_name, ancestors, path))
            else:
                directory = directory_selector(path)
                canonical_name = package.get("name") if isinstance(package.get("name"), str) else directory
                lock_nodes.append((directory, canonical_name, (), path))
else:
    lock_nodes = []
    root_selectors = set()
    import yaml
    lock = yaml.safe_load(Path("pnpm-lock.yaml").read_text(encoding="utf-8"))
    def pnpm_package_name(key):
        if not isinstance(key, str) or any(ord(char) < 32 for char in key):
            raise ValueError("pnpm package key must be a printable string")
        scoped = re.fullmatch(
            r"(@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)@(.+)", key)
        unscoped = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)@(.+)", key)
        match = scoped or unscoped
        if match is None:
            raise ValueError(f"invalid pnpm package key: {key}")
        reference = match.group(2)
        if reference.startswith("npm:"):
            raise ValueError(f"pnpm package alias is not an exact package identity: {key}")
        depth = 0
        for char in reference:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    raise ValueError(f"unbalanced pnpm peer context: {key}")
        if depth != 0:
            raise ValueError(f"unbalanced pnpm peer context: {key}")
        return match.group(1)
    package_sets = (lock.get("packages", {}), lock.get("snapshots", {}))
    if any(not isinstance(package_set, dict) for package_set in package_sets):
        sys.exit("pnpm lock packages and snapshots must be mappings")
    try:
        installed = {pnpm_package_name(key) for package_set in package_sets
                     for key in package_set}
        lifecycle_scoped = {
            pnpm_package_name(key)
            for package_set in package_sets
            for key, entry in package_set.items()
            if isinstance(entry, dict) and entry.get("requiresBuild") is True
        }
    except ValueError as error:
        sys.exit(str(error))
# A pnpm or npm lock records every platform variant of an optional
# native dependency (e.g. @esbuild/darwin-arm64, @esbuild/linux-x64),
# but --frozen-lockfile/--ignore-scripts installed only the ones this
# runner's os/cpu actually match. Scoping to the lock's full declared
# universe would force the allowlist to name a package that was never
# installed here, then fail rebuilding it (#941). node_modules/ on
# disk after that install is the platform-accurate source of truth.
lifecycle_scoped = {
    name for name in lifecycle_scoped
    if (Path("node_modules") / name).is_dir()
}
missing = sorted((set(requested) | set(skipped)) - installed)
if missing:
    sys.exit(("approved rebuild or skipped packages" if skipped else "approved rebuild packages")
             + " absent from lock: " + ", ".join(missing))
# npm rebuild selects nodes by their installed name and also rebuilds the
# bundled dependencies of every selected node. A skipped identity must be
# unreachable through either route, or the skip list would be a claim the
# package manager does not honor.
if skipped and root_selectors & set(requested):
    sys.exit(
        "secretless-rebuild-packages must not select the project root "
        "when secretless-skip-install-scripts is set: "
        + ", ".join(sorted(root_selectors & set(requested)))
    )
aliased_into_skip = sorted({
    selector for selector, canonical_name, _, _ in lock_nodes
    if selector in requested and canonical_name in skipped
})
if aliased_into_skip:
    sys.exit(
        "secretless-rebuild-packages selects npm aliases, links, or workspace "
        "directories of skipped packages: " + ", ".join(aliased_into_skip)
    )
bundled_skips = sorted({
    canonical_name for _, canonical_name, ancestors, _ in lock_nodes
    if canonical_name in skipped and any(ancestor in requested for ancestor in ancestors)
})
# The lock's inBundle flag is advisory; npm derives bundling from the
# host's own manifest, and normalizes aliases, object forms, and
# transitive bundles. Rather than reproduce that closure, refuse any
# selected host that declares bundled dependencies at all.
for selector, _, _, node_path in lock_nodes:
    if not skipped or selector not in requested:
        continue
    if not Path(node_path).is_dir():
        continue
    try:
        manifest = json.loads((Path(node_path) / "package.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        continue
    except (OSError, ValueError):
        sys.exit(f"cannot read the package manifest of {selector} to check its bundled dependencies")
    declared = manifest.get("bundleDependencies", manifest.get("bundledDependencies"))
    if declared:
        sys.exit(
            "a package selected by secretless-rebuild-packages declares bundled "
            "dependencies, which npm rebuild would rebuild as well, so it cannot "
            "be combined with secretless-skip-install-scripts: " + selector
        )
if skipped and package_manager == "npm" and Path("npm-shrinkwrap.json").exists():
    sys.exit("npm-shrinkwrap.json takes precedence over package-lock.json and is not validated; remove it or clear secretless-skip-install-scripts")
if bundled_skips:
    sys.exit(
        "skipped packages are bundled inside another package, so npm rebuild "
        "would run their install scripts: " + ", ".join(bundled_skips)
    )
# secretless-rebuild-packages is the complete, reviewed set of packages
# allowed to execute code at install time under --ignore-scripts (#932):
# an exact match, not a hint, so a dependency update that silently adds
# or drops a lifecycle script anywhere in the tree fails closed instead
# of passing unreviewed. secretless-skip-install-scripts is the reviewed
# set whose scripts are deliberately never run (#1888); it shares the
# accounting but never reaches the package manager.
undeclared = sorted(lifecycle_scoped - set(requested) - set(skipped))
if undeclared:
    sys.exit(
        "lock declares install scripts for packages absent from "
        "secretless-rebuild-packages and secretless-skip-install-scripts: "
        + ", ".join(undeclared)
    )
unnecessary = sorted(set(requested) - lifecycle_scoped)
if unnecessary:
    sys.exit(
        "secretless-rebuild-packages names packages the lock does not mark "
        "as needing install scripts: " + ", ".join(unnecessary)
    )
stale_skips = sorted(set(skipped) - lifecycle_scoped)
if stale_skips:
    sys.exit(
        "secretless-skip-install-scripts names packages the lock does not mark "
        "as needing install scripts: " + ", ".join(stale_skips)
    )
if skipped:
    print("install scripts deliberately skipped (never executed): " + ", ".join(sorted(skipped)), flush=True)
if not requested:
    sys.exit(0)
command = ["npm", "rebuild"] if package_manager == "npm" else ["corepack", "pnpm", "rebuild"]
if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
    sys.exit("secretless lifecycle rebuild requires a GitHub-hosted runner")
bubblewrap = Path(os.environ["BWRAP_BINARY"])
try:
    bubblewrap_metadata = bubblewrap.lstat()
    if (
        not stat.S_ISREG(bubblewrap_metadata.st_mode)
        or bubblewrap_metadata.st_uid not in (0, os.getuid())
        or bubblewrap_metadata.st_mode & (0o022 | stat.S_ISUID | stat.S_ISGID)
    ):
        sys.exit("trusted bubblewrap binary is unsafe")
    try:
        capabilities = os.getxattr(bubblewrap, "security.capability", follow_symlinks=False)
    except OSError as error:
        absent_capability = {errno.ENODATA}
        if hasattr(errno, "ENOATTR"):
            absent_capability.add(errno.ENOATTR)
        if error.errno not in absent_capability:
            raise
    else:
        if capabilities:
            sys.exit("trusted bubblewrap binary has file capabilities")
    workspace = Path.cwd().resolve(strict=True)
    node_modules = workspace / "node_modules"
    node_modules_metadata = node_modules.lstat()
    if not stat.S_ISDIR(node_modules_metadata.st_mode):
        sys.exit("secretless lifecycle rebuild requires a real node_modules directory")
    node_modules = node_modules.resolve(strict=True)
    if not node_modules.is_relative_to(workspace):
        sys.exit("node_modules resolves outside the checked-out workspace")
    home = Path(os.environ["HOME"]).resolve(strict=True)
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    if any(not path.is_dir() or path == Path("/") for path in (home, runner_temp)):
        sys.exit("trusted runner isolation paths are unsafe")
    tool_names = ("node", "npm") if package_manager == "npm" else ("node", "corepack")
    tool_paths = {}
    for name in tool_names:
        candidate = shutil.which(name)
        if candidate is None:
            sys.exit(f"trusted {name} executable is unavailable")
        tool_path = Path(candidate).resolve(strict=True)
        if not tool_path.is_file() or not os.access(tool_path, os.X_OK):
            sys.exit(f"trusted {name} executable is unsafe")
        tool_paths[name] = tool_path
    node_path = tool_paths["node"]
    if node_path.parent.name != "bin":
        sys.exit("trusted node executable has an unexpected installation layout")
    toolchain_root = node_path.parent.parent
    if (
        toolchain_root == Path("/")
        or toolchain_root == workspace
        or toolchain_root.is_relative_to(workspace)
        or workspace.is_relative_to(toolchain_root)
    ):
        sys.exit("trusted Node toolchain overlaps the checked-out workspace")
    if any(
        not path.is_relative_to(toolchain_root) and not path.is_relative_to("/usr")
        for path in tool_paths.values()
    ):
        sys.exit("trusted package-manager executable is outside the Node toolchain")
    toolchain_in_usr = toolchain_root.is_relative_to("/usr")
    runner_tool_cache = None
    if not toolchain_in_usr:
        runner_tool_cache = Path(os.environ["RUNNER_TOOL_CACHE"]).resolve(strict=True)
        if (
            not runner_tool_cache.is_dir()
            or not toolchain_root.is_relative_to(runner_tool_cache)
            or toolchain_root.is_relative_to(home)
            or home.is_relative_to(toolchain_root)
            or toolchain_root.is_relative_to(runner_temp)
            or runner_temp.is_relative_to(toolchain_root)
        ):
            sys.exit("trusted Node toolchain is outside the runner tool cache")
    toolchain_destination = str(toolchain_root) if toolchain_in_usr else "/opt/verjson-node-toolchain"
    toolchain_bin = f"{toolchain_destination}/bin"
    corepack_home = None
    if package_manager == "pnpm":
        configured_corepack_home = os.environ.get("COREPACK_HOME")
        corepack_candidate = Path(configured_corepack_home) if configured_corepack_home else Path.home() / ".cache/node/corepack"
        corepack_home = corepack_candidate.resolve(strict=True)
        if (
            not corepack_home.is_dir()
            or corepack_home != (home / ".cache/node/corepack").resolve(strict=True)
            or corepack_home == workspace
            or corepack_home.is_relative_to(workspace)
            or corepack_home == runner_temp
            or corepack_home.is_relative_to(runner_temp)
            or runner_temp.is_relative_to(corepack_home)
        ):
            sys.exit("trusted Corepack cache is unavailable")
    sandbox_workspace = "/tmp/" + sandbox_directory_name
    if any(
        runner_temp == path or runner_temp.is_relative_to(path)
        or path.is_relative_to(runner_temp)
        for path in (workspace,)
    ):
        sys.exit("runner temporary directory overlaps the workspace")
except OSError:
    sys.exit("trusted bubblewrap isolation is unavailable")

arguments = [
    str(bubblewrap),
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-net",
    "--unshare-cgroup-try",
    "--disable-userns",
    "--die-with-parent",
    "--new-session",
    "--cap-drop", "ALL",
    "--tmpfs", "/",
    "--tmpfs", "/tmp",
    "--dir", "/usr",
    "--dir", "/bin",
    "--dir", "/lib",
    "--dir", "/lib64",
    "--dir", "/etc",
    "--dir", "/etc/ssl",
    "--dir", "/opt",
]
mount_targets = [Path(sandbox_workspace), Path("/tmp/home"), Path("/tmp/npm-cache"), Path("/tmp/corepack")]
if not toolchain_in_usr:
    mount_targets.append(Path(toolchain_destination))
namespace_directories = set()
for target in mount_targets:
    namespace_directories.add(target)
    namespace_directories.update(target.parents)
namespace_directories.discard(Path("/"))
for directory in sorted(namespace_directories, key=lambda path: (len(path.parts), str(path))):
    if directory == Path("/usr") or directory.is_relative_to("/usr"):
        continue
    if directory in (Path("/tmp"), Path("/bin"), Path("/lib"), Path("/lib64"), Path("/etc"), Path("/etc/ssl"), Path("/opt")):
        continue
    arguments.extend(("--dir", str(directory)))

mount_fds = []
def bind_source(source, destination, *, writable=False):
    expected = source.stat()
    if not (stat.S_ISDIR(expected.st_mode) or stat.S_ISREG(expected.st_mode)):
        sys.exit("trusted sandbox mount source has an unsupported file type")
    open_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if stat.S_ISDIR(expected.st_mode):
        open_flags |= os.O_DIRECTORY
    descriptor = os.open(
        source,
        open_flags,
    )
    actual = os.fstat(descriptor)
    if (
        not (stat.S_ISDIR(actual.st_mode) or stat.S_ISREG(actual.st_mode))
        or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        os.close(descriptor)
        sys.exit("trusted sandbox mount source changed before namespace bind")
    os.set_inheritable(descriptor, True)
    mount_fds.append(descriptor)
    arguments.extend(("--bind" if writable else "--ro-bind", f"/proc/self/fd/{descriptor}", destination))

for source, destination in (
    (Path("/usr"), "/usr"),
    (Path("/bin").resolve(strict=True), "/bin"),
    (Path("/lib").resolve(strict=True), "/lib"),
    (Path("/lib64").resolve(strict=True), "/lib64"),
    # Alternatives-managed /usr names (awk, cc, c++) resolve here (#1782).
    (Path("/etc/alternatives"), "/etc/alternatives"),
    (Path("/etc/ssl"), "/etc/ssl"),
    (Path("/etc/hosts"), "/etc/hosts"),
    (Path("/etc/nsswitch.conf"), "/etc/nsswitch.conf"),
    (Path("/etc/passwd"), "/etc/passwd"),
    (Path("/etc/group"), "/etc/group"),
):
    bind_source(source, destination)
if not toolchain_in_usr:
    bind_source(toolchain_root, toolchain_destination)
if corepack_home is not None:
    bind_source(corepack_home, "/tmp/corepack")
bind_source(workspace, sandbox_workspace)
bind_source(node_modules, f"{sandbox_workspace}/node_modules", writable=True)
arguments.extend(("--dev", "/dev", "--proc", "/proc"))
arguments.extend(("--chdir", sandbox_workspace))

rebuild_process_env = {
    "PATH": f"{toolchain_bin}:/usr/bin:/bin",
    "HOME": "/tmp/home",
    "TMPDIR": "/tmp",
    "TMP": "/tmp",
    "TEMP": "/tmp",
    "RUNNER_TEMP": "/tmp",
    "GITHUB_WORKSPACE": sandbox_workspace,
    "CI": "true",
    "NPM_CONFIG_CACHE": "/tmp/npm-cache",
    "npm_config_cache": "/tmp/npm-cache",
    "COREPACK_HOME": "/tmp/corepack",
    "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
    # Egress is denied, so node-gyp builds against the selected
    # toolchain's own include/node headers instead of downloading
    # them (#1798). Every node-gyp version reads npm_config_*.
    "npm_config_nodedir": toolchain_destination,
    **rebuild_env,
}
for name, value in sorted(rebuild_process_env.items()):
    arguments.extend(("--setenv", name, value))
# The bootstrap imports nothing beyond the already-loaded os and sys
# before closing Bubblewrap's inherited host descriptors, and runs
# isolated (-I) because its working directory is the untrusted checkout.
# npm configs are created afterwards on the sandbox's fresh /tmp tmpfs.
sandbox_entrypoint = (
    "import os, sys\n"
    "max_fd = os.sysconf('SC_OPEN_MAX')\n"
    "if max_fd < 3: raise SystemExit('invalid file descriptor limit')\n"
    "os.closerange(3, max_fd)\n"
    "config_directory = '/tmp/npm-config'\n"
    "os.mkdir(config_directory, 0o700)\n"
    "for config_role in ('user', 'global'):\n"
    "    config_path = f'{config_directory}/{config_role}.npmrc'\n"
    "    os.close(os.open(\n"
    "        config_path,\n"
    "        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,\n"
    "        0o600,\n"
    "    ))\n"
    "    os.environ[f'NPM_CONFIG_{config_role.upper()}CONFIG'] = config_path\n"
    "    os.environ[f'npm_config_{config_role}config'] = config_path\n"
    "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)\n"
)
arguments.extend((
    "--",
    "/usr/bin/python3",
    "-I",
    "-c",
    sandbox_entrypoint,
    *command,
    *requested,
))
os.execve(str(bubblewrap), arguments, {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})

import hashlib
import json
import locale
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path, PurePosixPath

from loguru import logger

from .mod_manager import _download_progress, _format_size

EVEREST_VERSIONS_URL = "https://maddie480.ovh/celeste/everest-versions"
EVEREST_VERSIONS_MIRROR_URL = (
    "https://everestapi.github.io/updatermirror/everest_versions.json"
)

try:
    _PACKAGE_VERSION = metadata.version("celeste-mod-manager")
except metadata.PackageNotFoundError:
    _PACKAGE_VERSION = "unknown"

_USER_AGENT = f"celeste-mod-manager/{_PACKAGE_VERSION}"

_DOWNLOAD_MAX_ATTEMPTS = 3
_CATALOG_TIMEOUT_SECONDS = 20
_MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
_PAGE_SIZE = 20
_SUPPORTED_BRANCHES = {"stable", "beta", "dev"}
_PROTECTED_ROOT_NAMES = {"content", "mods", "orig", "saves"}
_VERSION_RE = re.compile(
    r"1\.(?P<build>[0-9]+)\.0-azure-(?P<commit>[0-9a-fA-F]{5})-"
    r"(?P<branch>stable|beta|dev)"
)
_GITHUB_RELEASE_RE = re.compile(
    r"^https://github\.com/EverestAPI/Everest/releases/download/"
    r"(?P<tag>[^/]+)/main\.zip$"
)
_AZURE_ARTIFACT_PATH_RE = re.compile(
    r"^/EverestAPI/Everest/_apis/build/builds/[0-9]+/artifacts$"
)


class EverestError(Exception):
    pass


class EverestStateKind(Enum):
    VANILLA = "vanilla"
    INSTALLED = "installed"
    UNKNOWN = "unknown"


class EverestAction(Enum):
    INSTALL = "Install"
    UPDATE = "Update"
    SWITCH = "Switch channel"
    REINSTALL = "Reinstall"
    DOWNGRADE = "Downgrade"


@dataclass(frozen=True)
class EverestVersion:
    build: int
    branch: str
    commit: str

    @property
    def version_string(self) -> str:
        return f"1.{self.build}.0"


@dataclass(frozen=True)
class EverestBuild:
    version: EverestVersion
    date: str
    main_download: str
    main_file_size: int
    description: str | None = None
    author: str | None = None


@dataclass(frozen=True)
class EverestInstallationState:
    kind: EverestStateKind
    version: EverestVersion | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ArchiveEntry:
    info: zipfile.ZipInfo
    relative_path: Path


def _request(url: str, timeout: int):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _download_json(url: str) -> object:
    with _request(url, _CATALOG_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _parse_build(raw: object) -> EverestBuild | None:
    if not isinstance(raw, dict):
        raise EverestError("Everest version catalog contains a non-object entry")
    is_native = raw.get("isNative")
    if not isinstance(is_native, bool):
        raise EverestError("Everest version catalog contains an invalid native flag")
    if not is_native:
        return None

    version = raw.get("version")
    branch = raw.get("branch")
    commit = raw.get("commit")
    date = raw.get("date")
    main_download = raw.get("mainDownload")
    main_file_size = raw.get("mainFileSize")

    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise EverestError("Everest version catalog contains an invalid build number")
    if branch not in _SUPPORTED_BRANCHES:
        raise EverestError(
            f"Everest version catalog contains an unsupported branch: {branch!r}"
        )
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise EverestError(
            f"Everest build {version} contains an invalid commit identifier"
        )
    if not isinstance(date, str) or not date:
        raise EverestError(f"Everest build {version} does not contain a valid date")
    if not isinstance(main_download, str):
        raise EverestError(f"Everest build {version} has no download URL")
    parsed_url = urllib.parse.urlparse(main_download)
    if parsed_url.scheme != "https" or parsed_url.port not in (None, 443):
        raise EverestError(f"Everest build {version} has an unsupported download URL")
    if parsed_url.hostname == "github.com":
        official_download = _GITHUB_RELEASE_RE.fullmatch(main_download) is not None
    elif parsed_url.hostname == "dev.azure.com":
        query = urllib.parse.parse_qs(parsed_url.query)
        official_download = _AZURE_ARTIFACT_PATH_RE.fullmatch(
            parsed_url.path
        ) is not None and query.get("artifactName") == ["main"]
    else:
        official_download = False
    if not official_download:
        raise EverestError(f"Everest build {version} has an unsupported download URL")
    if (
        not isinstance(main_file_size, int)
        or isinstance(main_file_size, bool)
        or main_file_size <= 0
    ):
        raise EverestError(f"Everest build {version} has an invalid download size")

    description = raw.get("description")
    author = raw.get("author")
    return EverestBuild(
        version=EverestVersion(version, branch, commit.lower()),
        date=date,
        main_download=main_download,
        main_file_size=main_file_size,
        description=description if isinstance(description, str) else None,
        author=author if isinstance(author, str) else None,
    )


def _parse_catalog(raw_catalog: object) -> list[EverestBuild]:
    if not isinstance(raw_catalog, list):
        raise EverestError("Everest version catalog is not a list")

    builds: list[EverestBuild] = []
    seen_builds: set[int] = set()
    for raw in raw_catalog:
        build = _parse_build(raw)
        if build is None:
            continue
        if build.version.build in seen_builds:
            raise EverestError(
                f"Everest version catalog contains duplicate build "
                f"{build.version.build}"
            )
        seen_builds.add(build.version.build)
        builds.append(build)

    builds.sort(key=lambda build: build.version.build, reverse=True)
    if not builds:
        raise EverestError("Everest version catalog contains no native builds")
    if not any(build.version.branch == "stable" for build in builds):
        raise EverestError("Everest version catalog contains no stable native build")
    return builds


def get_available_builds() -> list[EverestBuild]:
    errors: list[str] = []
    for index, url in enumerate((EVEREST_VERSIONS_URL, EVEREST_VERSIONS_MIRROR_URL)):
        try:
            builds = _parse_catalog(_download_json(url))
            if index > 0:
                print("Using the Everest version catalog mirror.", file=sys.stderr)
            return builds
        except Exception as e:
            errors.append(f"{url}: {e}")
            logger.opt(exception=e).debug(
                f"Failed to load Everest version catalog from '{url}'."
            )

    raise EverestError(
        "failed to load a valid Everest version catalog from both sources: "
        + "; ".join(errors)
    )


def _versions_in_bytes(data: bytes) -> list[EverestVersion]:
    versions: dict[tuple[int, str, str], EverestVersion] = {}
    for offset in (0, 1):
        text = data[offset:].decode("utf-16-le", errors="ignore")
        for match in _VERSION_RE.finditer(text):
            version = EverestVersion(
                build=int(match.group("build")),
                branch=match.group("branch"),
                commit=match.group("commit").lower(),
            )
            versions[(version.build, version.branch, version.commit)] = version
    return list(versions.values())


def _read_assembly_version(path: Path) -> EverestVersion | None:
    try:
        versions = _versions_in_bytes(path.read_bytes())
    except OSError as e:
        raise EverestError(f"failed to read '{path}': {e}") from e
    if not versions:
        return None
    if len(versions) != 1:
        rendered = ", ".join(version.version_string for version in versions)
        raise EverestError(
            f"found conflicting Everest versions in '{path}': {rendered}"
        )
    return versions[0]


def detect_installation(celeste_dir: Path) -> EverestInstallationState:
    celeste_dir = celeste_dir.resolve()
    celeste_exe = celeste_dir / "Celeste.exe"
    celeste_dll = celeste_dir / "Celeste.dll"
    everest_dll = celeste_dir / "Celeste.Mod.mm.dll"

    if not celeste_exe.is_file() and not celeste_dll.is_file():
        detail = f"neither Celeste.exe nor Celeste.dll was found in '{celeste_dir}'"
        return EverestInstallationState(
            EverestStateKind.UNKNOWN,
            detail=detail,
        )

    try:
        celeste_version = (
            _read_assembly_version(celeste_dll) if celeste_dll.is_file() else None
        )
        everest_version = (
            _read_assembly_version(everest_dll) if everest_dll.is_file() else None
        )
    except EverestError as e:
        return EverestInstallationState(EverestStateKind.UNKNOWN, detail=str(e))

    if celeste_version is not None:
        if everest_version is not None and everest_version != celeste_version:
            details = (
                f"{celeste_dll.name}={celeste_version.version_string}-"
                f"{celeste_version.branch}, "
                f"{everest_dll.name}={everest_version.version_string}-"
                f"{everest_version.branch}"
            )
            return EverestInstallationState(
                EverestStateKind.UNKNOWN,
                detail=f"installed Everest assemblies disagree: {details}",
            )
        return EverestInstallationState(EverestStateKind.INSTALLED, celeste_version)

    if everest_dll.exists() or celeste_dll.exists():
        return EverestInstallationState(
            EverestStateKind.UNKNOWN,
            detail=(
                "Everest-related files exist, but a patched Celeste.dll version "
                "could not be read; a previous installation may be incomplete"
            ),
        )

    return EverestInstallationState(EverestStateKind.VANILLA)


def classify_action(
    state: EverestInstallationState, target: EverestBuild
) -> EverestAction:
    current = state.version
    if current is None:
        return EverestAction.INSTALL
    if target.version.build == current.build:
        return EverestAction.REINSTALL
    if target.version.build < current.build:
        return EverestAction.DOWNGRADE
    if target.version.branch != current.branch:
        return EverestAction.SWITCH
    return EverestAction.UPDATE


def newest_builds_by_branch(builds: list[EverestBuild]) -> list[EverestBuild]:
    latest: dict[str, EverestBuild] = {}
    for build in builds:
        latest.setdefault(build.version.branch, build)
    return [latest[branch] for branch in ("stable", "beta", "dev") if branch in latest]


def page_count(builds: list[EverestBuild]) -> int:
    return max(1, (len(builds) + _PAGE_SIZE - 1) // _PAGE_SIZE)


def build_page(builds: list[EverestBuild], page: int) -> list[EverestBuild]:
    if page < 0 or page >= page_count(builds):
        return []
    start = page * _PAGE_SIZE
    return builds[start : start + _PAGE_SIZE]


def _download_build(build: EverestBuild, destination: Path) -> None:
    retryable_errors = (
        urllib.error.URLError,
        urllib.error.ContentTooShortError,
        TimeoutError,
        ConnectionError,
    )
    last_error: Exception | None = None

    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            destination.unlink(missing_ok=True)
            with _download_progress(build.main_file_size) as reporthook:
                urllib.request.urlretrieve(
                    build.main_download,
                    destination,
                    reporthook=reporthook,
                )
            return
        except retryable_errors as e:
            last_error = e
            logger.warning(
                f"Everest download attempt {attempt}/{_DOWNLOAD_MAX_ATTEMPTS} "
                f"failed: {e}"
            )
        except OSError as e:
            raise EverestError(f"failed to write the Everest download: {e}") from e
        except Exception as e:
            raise EverestError(f"failed to download Everest: {e}") from e

    raise EverestError(
        f"failed to download Everest after {_DOWNLOAD_MAX_ATTEMPTS} attempts: "
        f"{last_error}"
    )


def _github_release_sha256(build: EverestBuild) -> str | None:
    if build.version.branch != "stable":
        return None
    match = _GITHUB_RELEASE_RE.fullmatch(build.main_download)
    if match is None:
        return None

    tag = urllib.parse.quote(match.group("tag"), safe="")
    api_url = "https://api.github.com/repos/EverestAPI/Everest/releases/tags/" + tag
    try:
        release = _download_json(api_url)
    except Exception as e:
        logger.opt(exception=e).debug("Failed to load GitHub release digest.")
        print(
            "WARNING: could not retrieve the published SHA-256 digest; "
            "continuing with archive and embedded-version validation.",
            file=sys.stderr,
        )
        return None

    if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
        return None
    for asset in release["assets"]:
        if not isinstance(asset, dict) or asset.get("name") != "main.zip":
            continue
        digest = asset.get("digest")
        if digest is None:
            return None
        if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            return digest.split(":", 1)[1].lower()
        raise EverestError("GitHub published an invalid digest for main.zip")
    return None


def _calculate_sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as archive:
        while chunk := archive.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def _archive_entry(info: zipfile.ZipInfo) -> ArchiveEntry | None:
    name = info.filename.replace("\\", "/")
    source_path = PurePosixPath(name)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise EverestError(
            f"Everest archive contains an unsafe path: '{info.filename}'"
        )
    if not source_path.parts or source_path.parts[0] != "main":
        raise EverestError(
            f"Everest archive entry is outside the main directory: '{info.filename}'"
        )

    relative_parts = source_path.parts[1:]
    if not relative_parts:
        return None
    if any(":" in part for part in relative_parts):
        raise EverestError(
            f"Everest archive contains an unsafe path: '{info.filename}'"
        )
    if relative_parts[0].casefold() in _PROTECTED_ROOT_NAMES:
        raise EverestError(
            f"Everest archive attempts to write protected directory "
            f"'{relative_parts[0]}'"
        )

    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise EverestError(
            f"Everest archive contains an unsupported symbolic link: '{info.filename}'"
        )
    if not info.is_dir() and file_type not in (0, stat.S_IFREG):
        raise EverestError(
            f"Everest archive contains an unsupported special file: '{info.filename}'"
        )
    return ArchiveEntry(info, Path(*relative_parts))


def _platform_installer_name() -> str:
    system = platform.system()
    if system == "Windows":
        machine = platform.machine().lower()
        wow64 = os.environ.get("PROCESSOR_ARCHITEW6432", "").lower()
        is_64_bit = "64" in machine or "64" in wow64 or sys.maxsize > (2**32)
        return "MiniInstaller-win64.exe" if is_64_bit else "MiniInstaller-win.exe"
    if system == "Linux":
        return "MiniInstaller-linux"
    if system == "Darwin":
        return "MiniInstaller-osx"
    raise EverestError(f"unsupported operating system: {system}")


def ensure_supported_platform() -> None:
    _platform_installer_name()


def _validate_archive(archive_path: Path, build: EverestBuild) -> list[ArchiveEntry]:
    actual_size = archive_path.stat().st_size
    if actual_size != build.main_file_size:
        raise EverestError(
            f"Everest archive size mismatch: expected {build.main_file_size} bytes, "
            f"got {actual_size} bytes"
        )

    expected_sha256 = _github_release_sha256(build)
    if expected_sha256 is not None:
        actual_sha256 = _calculate_sha256(archive_path)
        if actual_sha256 != expected_sha256:
            raise EverestError(
                "Everest archive SHA-256 mismatch: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise EverestError(
                    f"Everest archive failed its CRC check at '{bad_entry}'"
                )

            entries: list[ArchiveEntry] = []
            total_size = 0
            seen_paths: set[str] = set()
            for info in archive.infolist():
                entry = _archive_entry(info)
                if entry is None:
                    continue
                canonical_path = entry.relative_path.as_posix().casefold()
                if canonical_path in seen_paths:
                    raise EverestError(
                        "Everest archive contains a duplicate path: "
                        f"'{entry.relative_path.as_posix()}'"
                    )
                seen_paths.add(canonical_path)
                total_size += info.file_size
                if total_size > _MAX_UNCOMPRESSED_SIZE:
                    raise EverestError("Everest archive is unexpectedly large")
                entries.append(entry)

            by_path = {entry.relative_path.as_posix(): entry for entry in entries}
            everest_entry = by_path.get("Celeste.Mod.mm.dll")
            installer_name = _platform_installer_name()
            if everest_entry is None:
                raise EverestError(
                    "Everest archive does not contain Celeste.Mod.mm.dll"
                )
            if installer_name not in by_path:
                raise EverestError(f"Everest archive does not contain {installer_name}")

            with archive.open(everest_entry.info) as everest_dll:
                versions = _versions_in_bytes(everest_dll.read())
            if len(versions) != 1:
                raise EverestError(
                    "Everest archive does not contain one unambiguous build identity"
                )
            embedded = versions[0]
            expected = build.version
            if embedded != EverestVersion(
                expected.build, expected.branch, expected.commit[:5]
            ):
                raise EverestError(
                    "Everest archive build identity mismatch: "
                    f"selected {expected.version_string}-{expected.branch}-"
                    f"{expected.commit[:5]}, archive contains "
                    f"{embedded.version_string}-{embedded.branch}-{embedded.commit}"
                )
            return entries
    except zipfile.BadZipFile as e:
        raise EverestError(f"downloaded Everest archive is not a valid ZIP: {e}") from e


def _extract_archive(
    archive_path: Path, entries: list[ArchiveEntry], staging_dir: Path
) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in entries:
                destination = staging_dir / entry.relative_path
                if entry.info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with (
                    archive.open(entry.info) as source,
                    destination.open("wb") as output,
                ):
                    shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile) as e:
        raise EverestError(f"failed to extract the Everest archive: {e}") from e


def _copy_staging(staging_dir: Path, celeste_dir: Path) -> None:
    try:
        sources = sorted(staging_dir.rglob("*"))
        for source in sources:
            relative = source.relative_to(staging_dir)
            destination = celeste_dir / relative
            resolved_destination = destination.resolve(strict=False)
            if not resolved_destination.is_relative_to(celeste_dir):
                raise EverestError(
                    "refusing to copy through a symbolic link outside Celeste: "
                    f"'{destination}'"
                )

        for source in sources:
            relative = source.relative_to(staging_dir)
            destination = celeste_dir / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f".{destination.name}.celeste-mod-manager.tmp"
            )
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as e:
        raise EverestError(f"failed to copy Everest files into Celeste: {e}") from e


def _run_miniinstaller(celeste_dir: Path) -> None:
    installer = celeste_dir / _platform_installer_name()
    try:
        if platform.system() != "Windows":
            installer.chmod(installer.stat().st_mode | stat.S_IXUSR)
    except OSError as e:
        raise EverestError(f"failed to make '{installer.name}' executable: {e}") from e

    print(f"Running {installer.name}...")
    try:
        process = subprocess.Popen(
            [str(installer)],
            cwd=celeste_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
            bufsize=1,
        )
    except OSError as e:
        raise EverestError(f"failed to start '{installer.name}': {e}") from e

    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line, end="")
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    return_code = process.wait()
    if return_code != 0:
        raise EverestError(
            f"{installer.name} failed with exit code {return_code}. "
            f"See '{celeste_dir / 'miniinstaller-log.txt'}' for details"
        )


def _verify_installation(celeste_dir: Path, target: EverestVersion) -> None:
    celeste_dll = celeste_dir / "Celeste.dll"
    if not celeste_dll.is_file():
        raise EverestError("MiniInstaller succeeded, but Celeste.dll was not created")
    installed = _read_assembly_version(celeste_dll)
    if installed is None:
        raise EverestError(
            "MiniInstaller succeeded, but the installed Everest version could not be read"
        )
    if installed != EverestVersion(target.build, target.branch, target.commit[:5]):
        raise EverestError(
            "MiniInstaller installed an unexpected Everest version: "
            f"expected {target.version_string}-{target.branch}-{target.commit[:5]}, "
            f"got {installed.version_string}-{installed.branch}-{installed.commit}"
        )

    if platform.system() == "Windows":
        launch_path = celeste_dir / "Celeste.exe"
    else:
        launch_path = celeste_dir / "Celeste"
    if not launch_path.is_file():
        raise EverestError(
            f"MiniInstaller succeeded, but the Celeste launcher '{launch_path}' is missing"
        )
    if platform.system() != "Windows" and not os.access(launch_path, os.X_OK):
        raise EverestError(
            f"MiniInstaller succeeded, but the Celeste launcher '{launch_path}' "
            "is not executable"
        )


def install_build(
    celeste_dir: Path,
    build: EverestBuild,
    delete_stale_orig: bool,
) -> None:
    celeste_dir = celeste_dir.resolve()
    with tempfile.TemporaryDirectory(
        prefix="celeste-mod-manager-everest-"
    ) as temporary_dir_name:
        temporary_dir = Path(temporary_dir_name)
        archive_path = temporary_dir / "main.zip"
        staging_dir = temporary_dir / "staging"
        staging_dir.mkdir()

        print(
            f"Downloading Everest {build.version.version_string} "
            f"({build.version.branch}, {_format_size(build.main_file_size)})"
        )
        _download_build(build, archive_path)
        print("Validating Everest archive...")
        entries = _validate_archive(archive_path, build)
        print("Extracting Everest archive...")
        _extract_archive(archive_path, entries, staging_dir)

        if delete_stale_orig:
            orig_dir = celeste_dir / "orig"
            try:
                if orig_dir.exists():
                    shutil.rmtree(orig_dir)
            except OSError as e:
                raise EverestError(
                    f"failed to delete stale backup directory '{orig_dir}': {e}"
                ) from e

        print("Copying Everest files into Celeste...")
        _copy_staging(staging_dir, celeste_dir)
        _run_miniinstaller(celeste_dir)
        try:
            _verify_installation(celeste_dir, build.version)
        except EverestError as e:
            raise EverestError(
                f"{e}. See '{celeste_dir / 'miniinstaller-log.txt'}' for details"
            ) from e

import os
import platform
from pathlib import Path
from typing import Optional
import vdf


def _get_steam_root() -> Optional[Path]:
    """
    Find Steam installation directory across platforms.
    Returns None if Steam is not found.
    """
    system = platform.system()

    # TODO: not tested on Windows.
    if system == "Windows":
        try:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                    install_path, _ = winreg.QueryValueEx(key, "SteamPath")
                    steam_root = Path(install_path)
                    if steam_root.exists():
                        return steam_root
            except FileNotFoundError:
                pass

            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam") as key:
                    install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                    steam_root = Path(install_path)
                    if steam_root.exists():
                        return steam_root
            except FileNotFoundError:
                pass
        except Exception:
            pass

        # Fallback to hardcoded locations
        candidates = [
            Path("C:\\Program Files\\Steam"),
            Path("C:\\Program Files (x86)\\Steam"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    elif system == "Darwin":  # macOS
        candidate = Path.home() / "Library" / "Application Support" / "Steam"
        if candidate.exists():
            return candidate
        return None

    elif system == "Linux":
        candidates = [
            Path.home() / ".steam" / "steam",
            Path.home() / ".local" / "share" / "Steam",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    return None


def steam_find_game(app_id: int) -> Optional[Path]:
    """
    Find the installation directory of a Steam game by its app ID.

    Args:
        app_id: The Steam app ID (e.g., 504230 for Celeste)

    Returns:
        The absolute path to the game directory, or None if not found.
    """
    steam_root = _get_steam_root()
    if not steam_root:
        return None

    # Try to find the game in libraryfolders.vdf
    libraryfolders_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if not libraryfolders_path.exists():
        return None

    with open(libraryfolders_path, "r", encoding="utf-8") as f:
        library_data = vdf.load(f)

    libraries = library_data.get("libraryfolders", {})
    # Search through all library folders
    for key in libraries:
        lib_data = libraries[key]
        apps = lib_data.get('apps', {})

        if str(app_id) in apps:
            library_path = lib_data.get('path')

            # 查找 manifest 文件以获取文件夹名称
            # manifest 文件名格式为 appmanifest_APPID.acf
            manifest_path = Path(library_path) / "steamapps" / f"appmanifest_{app_id}.acf"

            if manifest_path.exists():
                with open(manifest_path, 'r', encoding='utf-8') as mf:
                    manifest_data = vdf.parse(mf)
                    # 获取游戏的文件夹名称
                    folder_name = manifest_data.get('AppState', {}).get('installdir')

                    full_path = Path(library_path) / "steamapps" / "common" / folder_name
                    return full_path
    return None

if __name__ == "__main__":
    print(steam_find_game(504230))
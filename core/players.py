"""本机音乐播放器：识别已安装的 QQ 音乐 / 网易云音乐 / 酷狗音乐，并负责启动或切到前台。

只读注册表、进程列表和本机文件路径，不联网、不读播放内容。
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import psutil
import win32gui
import win32process

from .im import raise_window

# 系统卸载项 / 快捷方式里可能出现的名字
@dataclass(frozen=True)
class PlayerDef:
    key: str
    name: str
    exes: tuple
    paths: tuple
    keywords: tuple


PLAYERS: List[PlayerDef] = [
    PlayerDef(
        key="qqmusic", name="QQ音乐",
        exes=("qqmusic.exe",),
        paths=(
            r"C:\Program Files (x86)\Tencent\QQMusic\QQMusic.exe",
            r"C:\Program Files\Tencent\QQMusic\QQMusic.exe",
            r"C:\Program Files (x86)\Tencent\QQMusic\QQMusic.exe",
        ),
        keywords=("qq音乐", "qqmusic"),
    ),
    PlayerDef(
        key="cloudmusic", name="网易云音乐",
        exes=("cloudmusic.exe",),
        paths=(
            r"C:\Program Files\Netease\CloudMusic\cloudmusic.exe",
            r"C:\Program Files (x86)\Netease\CloudMusic\cloudmusic.exe",
            r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe",
        ),
        keywords=("网易云音乐", "cloudmusic"),
    ),
    PlayerDef(
        key="kugou", name="酷狗音乐",
        exes=("kugou.exe",),
        paths=(
            r"C:\Program Files (x86)\KuGou\KGMusic\KuGou.exe",
            r"C:\Program Files\KuGou\KGMusic\KuGou.exe",
            r"C:\Program Files (x86)\Kugou\KGMusic\KuGou.exe",
        ),
        keywords=("酷狗音乐", "kugou", "kgmusic"),
    ),
]


@dataclass
class PlayerInfo:
    key: str
    name: str
    path: Optional[str]
    running: bool


_cache: Dict[str, float] = {"t": 0.0}
_cache_value: List[PlayerInfo] = []
_EXPIRE = 20.0


def _clean_icon(value: str) -> Optional[str]:
    if not value:
        return None
    v = value.strip().strip('"')
    # DisplayIcon 常见形式：路径,0  或 路径,-1
    if v.lower().endswith((",0", ",-1")):
        v = v.rsplit(",", 1)[0].strip().strip('"')
    return v if os.path.exists(v) else None


def _registry_paths() -> Dict[str, str]:
    """从卸载表与 App Paths 里捞播放器可执行文件路径。"""
    found: Dict[str, str] = {}
    try:
        import winreg
    except ImportError:
        return found

    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for root in roots:
        for view in views:
            try:
                key = winreg.OpenKey(root, uninstall, 0,
                                     winreg.KEY_READ | view)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    name = winreg.EnumKey(key, i)
                    sub = winreg.OpenKey(key, name)
                    display, _ = winreg.QueryValueEx(sub, "DisplayName")
                    icon, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                except OSError:
                    continue
                low = str(display).lower()
                for p in PLAYERS:
                    if any(k in low for k in p.keywords) and p.key not in found:
                        path = _clean_icon(str(icon))
                        if path:
                            found[p.key] = path
    return found


def _running_paths() -> Dict[str, str]:
    found: Dict[str, str] = {}
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            exe = (proc.info["name"] or "").lower()
            for p in PLAYERS:
                if exe in p.exes and p.key not in found:
                    found[p.key] = proc.info["exe"] or ""
    except Exception:
        pass
    return found


def detect(force: bool = False) -> List[PlayerInfo]:
    """返回“本机实际可用”的播放器（不区分是否正在运行）。"""
    now = time.monotonic()
    if not force and _cache_value and now - _cache["t"] < _EXPIRE:
        return list(_cache_value)

    running = _running_paths()
    reg = _registry_paths()
    result: List[PlayerInfo] = []
    for p in PLAYERS:
        path = running.get(p.key) or reg.get(p.key)
        if not path:
            for cand in p.paths:
                if os.path.exists(cand):
                    path = cand
                    break
        if path:
            result.append(PlayerInfo(p.key, p.name, path, p.key in running))
    _cache["t"] = now
    _cache_value[:] = result
    return list(result)


def _find_main_hwnd(key: str, exes: tuple) -> int:
    """找到该播放器进程里面积最大的可见顶层窗口。"""
    targets = set()
    for proc in psutil.process_iter(["name", "pid"]):
        if (proc.info["name"] or "").lower() in exes:
            targets.add(proc.info["pid"])
    if not targets:
        return 0
    best = (0, 0)

    def cb(hwnd, _):
        nonlocal best
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return True
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if pid not in targets:
                return True
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if w > 120 and h > 80 and w * h > best[0]:
                best = (w * h, hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return best[1]


def open_player(key: str) -> bool:
    """已开就切到前台，没开就启动。返回是否成功。"""
    p = next((x for x in PLAYERS if x.key == key), None)
    if p is None:
        return False
    hwnd = _find_main_hwnd(key, p.exes)
    if hwnd:
        return raise_window(hwnd)
    info = next((x for x in detect(force=True) if x.key == key), None)
    path = info.path if info else None
    if not path or not os.path.exists(path):
        return False
    try:
        subprocess.Popen([path], cwd=os.path.dirname(path), close_fds=True)
        return True
    except Exception:
        try:
            os.startfile(path)  # noqa: S606 - 启动本机已安装程序
            return True
        except Exception:
            return False

"""本地配置：存于 %APPDATA%\\Xiaoer\\config.json，并管理开机自启注册表项。

老名字 DaozaiPython 的数据目录与自启项在首次启动时自动搬到新名字下，
避免升级后丢歌词缓存或两个 exe 同时开机自启。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import winreg
from pathlib import Path

APP_NAME = "Xiaoer"                 # 数据目录名（%APPDATA%\Xiaoer）
LEGACY_APP_NAME = "DaozaiPython"    # 改名前的数据目录
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Xiaoer"
LEGACY_RUN_VALUE = "DaozaiPython"


def _migrate_legacy_dir(base: Path) -> None:
    """把老数据目录整体搬到新目录（只在新目录还不存在时做一次）。"""
    old = base / LEGACY_APP_NAME
    new = base / APP_NAME
    if new.exists() or not old.exists():
        return
    try:
        old.rename(new)
    except OSError:
        try:
            shutil.copytree(old, new)
        except Exception:
            pass


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    _migrate_legacy_dir(Path(base))
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return config_dir() / "config.json"


DEFAULTS = {
    "autostart": False,
    "wechat_enabled": True,
    "qq_enabled": True,
    "wecom_enabled": True,
    "feishu_enabled": True,
    "dingtalk_enabled": True,
    "edge_hide": True,
    "show_lyrics": True,
    "edge_hide_delay_ms": 500,
    "media_enabled": True,
    "weather_enabled": False,
    "clip_enabled": True,          # 记录最近复制的文字
    "clip_limit": 30,              # 未固定的最多留几条
    "snip_hotkey": "ctrl+alt+x",   # 区域截图全局热键，留空则不注册
    "snip_dir": "",                # 截图保存目录，空 = 图片\小二截图
    "accent": "#2d7dff",
    "capsule_tools": ["clip", "snip", "bell", "list", "bejson", "excel"],
    "dock": "top",  # top / left / right
}


def load() -> dict:
    data = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            data.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return data


def save(cfg: dict) -> None:
    try:
        config_path().write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _launch_command() -> str:
    """开发态用 python 解释器跑 main.py；打包态直接指向 exe。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return f'"{sys.executable}"'
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{main_py}"'


def migrate_autostart() -> None:
    """老名字的自启项换成新名字：新名字已有就只删老的，避免两个 exe 同时开机启动。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS
        ) as key:
            try:
                winreg.QueryValueEx(key, LEGACY_RUN_VALUE)
            except OSError:
                return
            new_exists = True
            try:
                winreg.QueryValueEx(key, RUN_VALUE)
            except OSError:
                new_exists = False
            if not new_exists:
                winreg.SetValueEx(
                    key, RUN_VALUE, 0, winreg.REG_SZ, _launch_command()
                )
            try:
                winreg.DeleteValue(key, LEGACY_RUN_VALUE)
            except OSError:
                pass
    except OSError:
        pass


def is_autostart() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            val, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(val)
    except OSError:
        return False


def set_autostart(enable: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enable:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except OSError:
                pass

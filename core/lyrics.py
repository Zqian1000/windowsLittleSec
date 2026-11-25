"""歌曲歌词：按“歌名 + 歌手”取值，解析 LRC 并按播放进度取当前一句。

数据来自网易云音乐的公开歌词接口（只发歌名/歌手，不上传本地任何信息），
取到后缓存在 %APPDATA%\\Xiaoer\\lyrics\\ 下，同一首歌再播放不联网。
取不到时静默返回空，界面不会因此报错。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_HEADERS = {"User-Agent": _UA, "Referer": "https://music.163.com/"}

_TAG = re.compile(r"\[(\d+):(\d+)(?:[.:](\d+))?\]")
_cache_lock = threading.Lock()
_mem: dict = {}          # key -> (mtime, lines)
_pending: set = set()
_durations: dict = {}    # key -> 曲长（秒），取封面时顺带记下来


def _cache_dir() -> str:
    try:
        from .config import config_dir
        path = str(config_dir() / "lyrics")
    except Exception:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "Xiaoer", "lyrics")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _key(title: str, artist: str) -> str:
    return hashlib.md5(f"{title}|{artist}".encode("utf-8")).hexdigest()[:16]


def parse_lrc(text: str) -> list:
    """把 LRC 文本切成 [(秒, 歌词)]，丢弃只有时间的空行。"""
    out = []
    for raw in (text or "").splitlines():
        tags = _TAG.findall(raw)
        content = _TAG.sub("", raw).strip()
        if not tags or not content:
            continue
        for mm, ss, frac in tags:
            ms = (frac or "0").ljust(3, "0")[:3]
            t = int(mm) * 60 + int(ss) + int(ms) / 1000.0
            out.append((t, content))
    out.sort(key=lambda x: x[0])
    # 去掉连续的重复句（有些歌词每句打两个时间戳）
    dedup = []
    for t, line in out:
        if dedup and dedup[-1][1] == line:
            continue
        dedup.append((t, line))
    return dedup


def _http_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _search_song_id(title: str, artist: str):
    query = f"{title} {artist}".strip()
    url = ("https://music.163.com/api/search/get?s="
           + urllib.parse.quote(query) + "&type=1&limit=5")
    data = _http_json(url)
    songs = ((data.get("result") or {}).get("songs") or [])
    best, best_score = None, 0.0
    for s in songs:
        name = (s.get("name") or "").strip()
        artists = " ".join(a.get("name", "") for a in (s.get("artists") or []))
        score = 0.0
        if name and name in title or title and title in name:
            score += 2.0
        if artist and artist.split(" ")[0] and artist.split(" ")[0] in artists:
            score += 1.5
        if score > best_score:
            best, best_score = s.get("id"), score
    if best is None and songs:
        best = songs[0].get("id")
    return best


def _fetch_lines(title: str, artist: str) -> list:
    sid = _search_song_id(title, artist)
    if not sid:
        return []
    url = f"https://music.163.com/api/song/lyric?id={sid}&lv=-1&kv=-1&tv=-1"
    data = _http_json(url)
    lyric = ((data.get("lrc") or {}).get("lyric") or "")
    return parse_lrc(lyric)


def cached(title: str, artist: str) -> list:
    """只看缓存，不联网（界面线程用）。"""
    if not title:
        return []
    key = _key(title, artist)
    with _cache_lock:
        if key in _mem:
            return _mem[key]
    path = os.path.join(_cache_dir(), key + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [(float(t), str(s)) for t, s in json.load(f)]
    except Exception:
        return []
    with _cache_lock:
        _mem[key] = lines
    return lines


def load(title: str, artist: str) -> list:
    """同步取歌词（带内存 + 磁盘缓存）。取不到返回空列表。"""
    if not title:
        return []
    key = _key(title, artist)
    with _cache_lock:
        hit = _mem.get(key)
    if hit is not None:
        return hit
    path = os.path.join(_cache_dir(), key + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [(float(t), str(s)) for t, s in json.load(f)]
        with _cache_lock:
            _mem[key] = lines
        return lines
    except Exception:
        pass
    try:
        lines = _fetch_lines(title, artist)
    except Exception:
        lines = []
    if lines:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump([[t, s] for t, s in lines], f, ensure_ascii=False)
        except Exception:
            pass
    with _cache_lock:
        _mem[key] = lines        # 空结果也缓存，避免反复联网
    return lines


def load_async(title: str, artist: str) -> None:
    """后台拉歌词，界面线程不用等网络。"""
    key = _key(title, artist)
    with _cache_lock:
        if key in _pending:
            return
        _pending.add(key)
    threading.Thread(target=lambda: _bg(title, artist, key), daemon=True).start()


def _bg(title: str, artist: str, key: str) -> None:
    try:
        load(title, artist)
        ensure_duration(title, artist)      # 顺带把曲长备好，进度换算要用
    finally:
        with _cache_lock:
            _pending.discard(key)


def line_at(lines: list, position: float) -> str:
    """按当前进度取这一句；还没到第一句时给第一句的空档返回空串。"""
    if not lines:
        return ""
    # 二分找最后一个时间 <= position
    lo, hi = 0, len(lines) - 1
    idx = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid][0] <= position:
            idx, lo = mid, mid + 1
        else:
            hi = mid - 1
    if idx < 0:
        return ""
    return lines[idx][1]

# ---------------------------------------------------------------------------
# 封面（SMTC 没会话时的兜底）
# ---------------------------------------------------------------------------
def _cover_path(key: str) -> str:
    return os.path.join(_cache_dir(), f"cover_{key}.bin")


def _dur_path(key: str) -> str:
    return os.path.join(_cache_dir(), f"dur_{key}.json")


def _remember_duration(key: str, ms) -> None:
    """把接口给的毫秒曲长记下来（内存 + 磁盘），失败静默。"""
    try:
        secs = float(ms or 0) / 1000.0
    except Exception:
        return
    if secs <= 0:
        return
    _durations[key] = secs
    try:
        with open(_dur_path(key), "w", encoding="utf-8") as f:
            json.dump(secs, f)
    except Exception:
        pass


def cached_duration(title: str, artist: str) -> float:
    """曲长（秒），只看缓存；没有就返回 0，界面不会因此等网络。"""
    if not title:
        return 0.0
    key = _key(title, artist)
    if key in _durations:
        return _durations[key]
    try:
        with open(_dur_path(key), "r", encoding="utf-8") as f:
            secs = float(json.load(f))
    except Exception:
        return 0.0
    if secs > 0:
        _durations[key] = secs
    return max(0.0, secs)


def ensure_duration(title: str, artist: str, timeout: float = 6.0) -> float:
    """后台取曲长：只按歌名+歌手搜一次，取到就记下来。取不到返回 0。"""
    if not title or cached_duration(title, artist) > 0:
        return cached_duration(title, artist)
    key = _key(title, artist)
    try:
        query = f"{title} {artist}".strip()
        url = ("https://music.163.com/api/search/get?s="
               + urllib.parse.quote(query) + "&type=1&limit=5")
        data = _http_json(url, timeout)
        songs = ((data.get("result") or {}).get("songs") or [])
    except Exception:
        return 0.0
    best, best_score = None, -1.0
    for s in songs:
        name = (s.get("name") or "").strip()
        artists = " ".join(a.get("name", "") for a in (s.get("artists") or []))
        score = 0.0
        if name and (name in title or title in name):
            score += 2.0
        if artist and artist.split(" ")[0] and artist.split(" ")[0] in artists:
            score += 1.5
        if score > best_score:
            best, best_score = s, score
    if best is None:
        return 0.0
    _remember_duration(key, best.get("duration"))
    return cached_duration(title, artist)


def fetch_cover(title: str, artist: str, timeout: float = 6.0) -> bytes:
    """按歌名+歌手在网易云搜一次，取专辑封面图并缓存到磁盘。

    搜索/详情里带的曲长顺手记进 _durations，供进度换算用（不额外发请求）。
    """
    if not title:
        return b""
    cache_key = _key(title, artist)
    path = _cover_path(cache_key)
    try:
        if os.path.exists(path):
            data = open(path, "rb").read()
            if data:
                return data
    except Exception:
        pass

    query = f"{title} {artist}".strip()
    url = ("https://music.163.com/api/search/get?s="
           + urllib.parse.quote(query) + "&type=1&limit=5")
    try:
        data = _http_json(url, timeout)
        songs = ((data.get("result") or {}).get("songs") or [])
    except Exception:
        return b""
    if not songs:
        return b""

    best, best_score = None, -1.0
    for s in songs:
        name = (s.get("name") or "").strip()
        artists = " ".join(a.get("name", "") for a in (s.get("artists") or []))
        score = 0.0
        if name and (name in title or title in name):
            score += 2.0
        if artist and artist.split(" ")[0] and artist.split(" ")[0] in artists:
            score += 1.5
        if score > best_score:
            best, best_score = s, score
    pic = ((best or {}).get("album") or {}).get("picUrl") or ""
    _remember_duration(cache_key, (best or {}).get("duration"))
    if not pic:
        # 搜索接口不带专辑封面，按歌曲 id 再查一次详情
        sid = (best or {}).get("id")
        if sid:
            try:
                detail = _http_json(
                    f"https://music.163.com/api/song/detail?id={sid}&ids=[{sid}]", timeout)
                ss = detail.get("songs") or []
                if ss:
                    pic = (ss[0].get("album") or {}).get("picUrl") or ""
                    _remember_duration(cache_key, ss[0].get("duration"))
            except Exception:
                pic = ""
    if not pic:
        return b""

    try:
        pic = pic.split("?")[0] + "?param=100y100"
        req = urllib.request.Request(pic, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4 * 1024 * 1024 + 1)
    except Exception:
        return b""
    if not raw or len(raw) > 4 * 1024 * 1024:
        return b""
    try:
        open(path, "wb").write(raw)
    except Exception:
        pass
    return raw

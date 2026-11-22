"""YouTube 來源：網址解析與驗證、字幕語言選擇、yt-dlp 錯誤訊息對應（純 Python，可單元測試）。

防 SSRF：使用者貼的網址只用來解析出影片 ID／播放清單 ID，交給 yt-dlp 的一律是我們自己組出來的
`https://www.youtube.com/watch?v=<ID>` 或 `https://www.youtube.com/playlist?list=<ID>`，
所以 yt-dlp 不會去連使用者指定的其他網址。
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .i18n import DEFAULT_LANG, LocalizedError, Message, has, localize, msg

# 只接受這些網域（不含子網域萬用字元，避免 evil.youtube.com.example 這類）
ALLOWED_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
})
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
# 路徑第一段是這些時，第二段是影片 ID：/shorts/<id>、/live/<id>、/embed/<id>、/v/<id>
_VIDEO_PATH_PREFIXES = ("shorts", "live", "embed", "v", "e")
# YouTube 自動產生的「合輯」（Mix，RD 開頭）是無限延伸的清單，有影片 ID 時只當成單一影片
_MIX_PREFIXES = ("RD", "UL")

MAX_URL_LENGTH = 2048


class InvalidUrl(LocalizedError, ValueError):
    """網址不是可以處理的 YouTube 網址；訊息（msg）可以直接顯示給使用者。"""


@dataclass(frozen=True)
class ParsedUrl:
    kind: str  # video / playlist
    video_id: str | None = None
    playlist_id: str | None = None

    @property
    def url(self) -> str:
        """交給 yt-dlp 的正規網址（不使用使用者原本的網址）。"""
        if self.kind == "playlist":
            return playlist_url(self.playlist_id or "")
        return video_url(self.video_id or "")


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def parse_url(raw: str) -> ParsedUrl:
    """解析 youtube.com/watch、youtu.be、shorts、live、embed 與播放清單（list=）網址。"""
    s = (raw or "").strip()
    if not s:
        raise InvalidUrl("youtube.url.empty")
    if len(s) > MAX_URL_LENGTH:
        raise InvalidUrl("youtube.url.too_long")
    if "://" not in s:
        s = "https://" + s
    try:
        u = urllib.parse.urlsplit(s)
        port = u.port
    except ValueError as e:
        raise InvalidUrl("youtube.url.malformed") from e
    if u.scheme.lower() not in ("http", "https"):
        raise InvalidUrl("youtube.url.scheme")
    if u.username is not None or u.password is not None:
        raise InvalidUrl("youtube.url.malformed")
    host = (u.hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS or port not in (None, 80, 443):
        raise InvalidUrl("youtube.url.not_youtube")

    query = urllib.parse.parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    video_id: str | None = None
    if host.endswith("youtu.be"):
        video_id = parts[0] if parts else None
    elif parts[:1] == ["watch"]:
        video_id = (query.get("v") or [None])[0]
    elif len(parts) >= 2 and parts[0] in _VIDEO_PATH_PREFIXES:
        video_id = parts[1]
    if video_id is not None and not VIDEO_ID_RE.match(video_id):
        video_id = None

    list_id = (query.get("list") or [None])[0]
    if list_id is not None and not PLAYLIST_ID_RE.match(list_id):
        list_id = None
    if list_id and not (video_id and list_id.startswith(_MIX_PREFIXES)):
        return ParsedUrl(kind="playlist", playlist_id=list_id, video_id=video_id)
    if video_id:
        return ParsedUrl(kind="video", video_id=video_id)
    raise InvalidUrl("youtube.url.unrecognized")


_TOKEN_RE = re.compile(r"[^\s,，]+")


def collect_lines(text: str, limit: int = 50) -> tuple[list[str], bool]:
    """一次貼多個網址：換行（也接受空白、逗號）分隔，去掉重複與空白行。

    回傳（最多 limit 筆, 是否還有更多）。收集到 limit+1 筆就停止，不會把整段 200 KB 的文字全部切開。
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        token = m.group(0)
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) > limit:
            return out[:limit], True
    return out, False


def split_lines(text: str, limit: int = 50) -> list[str]:
    return collect_lines(text, limit)[0]


# ---- 字幕語言 ----

# 依序優先：zh-TW、zh-Hant、zh、zh-Hans、en、影片原始語言。
# 每一層：(語言代碼, 是否接受「代碼-其他」的變體，例如 en-US、zh-TW-xxx)
CAPTION_TIERS: list[list[tuple[str, bool]]] = [
    [("zh-TW", True)],
    [("zh-Hant", True), ("zh-HK", True), ("zh-MO", True)],
    [("zh", False)],
    [("zh-Hans", True), ("zh-CN", True), ("zh-SG", True)],
    [("en", True)],
]

def language_param(code: str | None) -> Message | str:
    """語言名稱（放進訊息參數用）：認得的語言是訊息（依介面語言顯示），認不得的照原本的代碼。

    名稱表在 i18n.MESSAGES 的 `language.<代碼小寫>`；先找完整代碼（zh-TW），再找主要語言（en-US → en）。
    """
    if not code:
        return ""
    key = code.lower()
    if has(f"language.{key}"):
        return msg(f"language.{key}")
    base = key.split("-")[0]
    if has(f"language.{base}"):
        return msg(f"language.{base}")
    return code


def language_name(code: str | None, lang: str = DEFAULT_LANG) -> str:
    return localize(language_param(code), lang)


def caption_label(lang: str | None) -> Message:
    """結果頁的文字來源說明，例如「YouTube 人工字幕（台灣繁體中文）」（可延後翻譯的訊息）。"""
    name = language_param(lang)
    return msg("youtube.caption_label", language=name) if name else msg("youtube.caption_label_plain")


def _matches(code: str, base: str, prefix: bool) -> bool:
    c, b = code.lower(), base.lower()
    return c == b or (prefix and c.startswith(b + "-"))


def choose_caption(langs: list[str], original_language: str | None = None) -> str | None:
    """從人工字幕的語言清單挑一個；沒有符合優先順序的回傳 None（改用模型轉錄）。

    同一層有多個時：完全相同的代碼優先，其次照清單順序。自動字幕（automatic_captions）不要傳進來。
    """
    langs = [x for x in langs if x and x != "live_chat"]
    tiers = list(CAPTION_TIERS)
    if original_language:
        tiers.append([(original_language.split("-")[0], True)])
    for tier in tiers:
        for base, prefix in tier:
            exact = [x for x in langs if x.lower() == base.lower()]
            if exact:
                return exact[0]
        for base, prefix in tier:
            for x in langs:
                if _matches(x, base, prefix):
                    return x
    return None


def manual_caption_langs(info: dict[str, Any]) -> list[str]:
    """yt-dlp 資訊裡的人工字幕語言（`subtitles`）；`automatic_captions` 是自動字幕，不採用。"""
    subs = info.get("subtitles") or {}
    return [k for k, v in subs.items() if k != "live_chat" and v]


def video_summary(info: dict[str, Any]) -> dict[str, Any]:
    """整理 yt-dlp 的影片資訊（-J 輸出），只留前端與任務需要的欄位。"""
    langs = manual_caption_langs(info)
    language = info.get("language") or None
    vid = info.get("id") or ""
    duration = info.get("duration")
    return {
        "video_id": vid,
        "title": info.get("title") or vid,
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        "live_status": info.get("live_status") or None,
        "availability": info.get("availability") or None,
        "language": language,
        "manual_captions": langs,
        "caption_lang": choose_caption(langs, language),
        "url": video_url(vid),
    }


# ---- 錯誤訊息 ----


class YouTubeError(RuntimeError):
    """YouTube 無法處理（私人影片、地區限制、下載失敗等）。

    msg 是給使用者看的說明（可延後翻譯的訊息，舊寫法也可以直接給文字）；message 是它的 zh-TW 文字（log 用）。
    """

    def __init__(self, code: str, message: Message | str, status: int = 422, detail: str = "") -> None:
        self.msg: Message | str = message
        self.message: str = localize(message, DEFAULT_LANG)
        super().__init__(self.message)
        self.code = code
        self.status = status  # API 回應碼：422＝影片本身的問題、502＝連線／YouTube 端暫時的問題
        # yt-dlp 原始錯誤的最後一行（已遮掉路徑），記 log 與組警告訊息用（例如取出 HTTP 狀態碼）
        self.detail = detail


# (代碼, 比對字串（小寫）, HTTP 狀態)；由上往下第一個符合的為準。給使用者看的說明是 i18n 的 youtube.error.<代碼>
_ERROR_RULES: list[tuple[str, tuple[str, ...], int]] = [
    ("private", ("private video", "this video is private"), 422),
    ("members_only", ("members-only", "join this channel", "channel's members", "members only"), 422),
    ("age_restricted", ("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"), 422),
    ("geo_restricted", ("not made this video available in your country", "not available in your country",
                        "from your location", "geo restriction", "geo-restrict"), 422),
    ("live_not_ended", ("live event will begin", "premieres in", "premiere will begin", "is_upcoming",
                        "this live event", "live stream recording is not available", "is_live", "post_live"), 422),
    ("payment", ("requires payment", "purchase", "rental"), 422),
    ("playlist_missing", ("the playlist does not exist", "playlist does not exist"), 422),
    # 只比對 YouTube 對影片本身的說明；不能用「not found」「does not exist」這種寬鬆的字，
    # 否則格式網址的 HTTP 404 也會被誤判成影片下架
    ("removed", ("has been removed", "video unavailable", "this video is unavailable", "no longer available",
                 "account associated with this video has been terminated", "this video does not exist",
                 "video does not exist", "this video isn't available anymore"), 422),
    ("bot_check", ("not a bot", "captcha", "sign in to confirm"), 502),
    ("rate_limited", ("try again later", "http error 429", "too many requests", "rate-limited"), 502),
    ("expired", ("http error 404", "http error 410"), 502),
    ("forbidden", ("http error 403", "403: forbidden"), 502),
    ("network", ("unable to download webpage", "unable to download api page", "name resolution",
                 "temporary failure", "network is unreachable", "timed out", "connection reset",
                 "connection refused", "urlopen error", "read timed out", "getaddrinfo failed"), 502),
    ("too_large", ("larger than max-filesize", "file is larger than"), 422),
    ("no_format", ("requested format is not available", "no video formats found"), 502),
]


def last_error_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("ERROR:")]
    line = errors[-1] if errors else (lines[-1] if lines else "")
    return line.removeprefix("ERROR:").strip()


# 絕對路徑（/data/jobs/... 這類）：前一個字元不能是英數字、「.」「:」「/」「-」，網址裡的路徑不會被誤遮
_ABS_PATH_RE = re.compile(r"(?<![\w.:/-])/(?:[^\s/'\"]+/)+([^\s/'\"]*)")


def mask_paths(text: str) -> str:
    """遮掉伺服器上的絕對路徑，只留檔名（錯誤訊息會顯示在 UI 上）。"""
    return _ABS_PATH_RE.sub(lambda m: f"…/{m.group(1)}" if m.group(1) else "…/", text or "")


def classify_error(text: str) -> YouTubeError:
    """把 yt-dlp 的錯誤輸出（stderr）對應成使用者看得懂的訊息。"""
    line = last_error_line(text)
    low = (line or text or "").lower()
    detail = mask_paths(line)[:300]
    for code, needles, status in _ERROR_RULES:
        if any(n in low for n in needles):
            return YouTubeError(code, _message(code), status, detail=detail)
    return unknown_error(detail)


def unknown_error(detail: str | Message, status: int = 502) -> YouTubeError:
    """沒有對應規則的下載失敗：「YouTube 下載失敗：<原因>」。detail 可以是 yt-dlp 的原文或訊息。"""
    shown = detail or msg("youtube.error.unknown_detail")
    raw = detail if isinstance(detail, str) else localize(detail, DEFAULT_LANG)
    return YouTubeError("unknown", msg("youtube.error.unknown", detail=shown), status, detail=raw)


def check_playable(summary: dict[str, Any]) -> None:
    """直播中、尚未開始、剛結束還在處理的影片不能下載；會員限定等也在這裡擋（有資訊時）。"""
    live = summary.get("live_status")
    if live == "is_live":
        raise YouTubeError("live_not_ended", msg("youtube.error.live_in_progress"))
    if live == "is_upcoming":
        raise YouTubeError("live_not_ended", msg("youtube.error.live_upcoming"))
    if live == "post_live":
        raise YouTubeError("live_not_ended", msg("youtube.error.post_live"))
    availability = summary.get("availability")
    if availability == "subscriber_only":
        raise YouTubeError("members_only", _message("members_only"))
    if availability == "premium_only":
        raise YouTubeError("payment", msg("youtube.error.premium_only"))
    if availability == "needs_auth":
        raise YouTubeError("age_restricted", _message("age_restricted"))
    if availability == "private":
        raise YouTubeError("private", _message("private"))


def _message(code: str) -> Message:
    return msg(f"youtube.error.{code}")


def safe_filename(title: str, fallback: str = "transcript", limit: int = 120) -> str:
    """匯出用的檔名：拿掉路徑分隔與控制字元（影片標題裡常有 /、: 之類的字元）。"""
    s = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", title or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip() or fallback

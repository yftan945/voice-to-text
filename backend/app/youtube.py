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


class InvalidUrl(ValueError):
    """網址不是可以處理的 YouTube 網址；訊息可以直接顯示給使用者。"""


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
        raise InvalidUrl("網址是空的")
    if len(s) > MAX_URL_LENGTH:
        raise InvalidUrl("網址太長")
    if "://" not in s:
        s = "https://" + s
    try:
        u = urllib.parse.urlsplit(s)
        port = u.port
    except ValueError as e:
        raise InvalidUrl("網址格式錯誤") from e
    if u.scheme.lower() not in ("http", "https"):
        raise InvalidUrl("只接受 http／https 的 YouTube 網址")
    if u.username is not None or u.password is not None:
        raise InvalidUrl("網址格式錯誤")
    host = (u.hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS or port not in (None, 80, 443):
        raise InvalidUrl("只接受 YouTube 網址（youtube.com、youtu.be）")

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
    raise InvalidUrl("看不出是哪一部影片或播放清單，請貼影片或播放清單的網址")


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

LANGUAGE_NAMES = {
    "zh-tw": "台灣繁體中文", "zh-hant": "繁體中文", "zh-hk": "香港繁體中文", "zh-mo": "澳門繁體中文",
    "zh": "中文", "zh-hans": "簡體中文", "zh-cn": "簡體中文", "zh-sg": "簡體中文（新加坡）",
    "en": "英文", "ja": "日文", "ko": "韓文", "fr": "法文", "de": "德文", "es": "西班牙文",
    "vi": "越南文", "th": "泰文", "id": "印尼文", "ms": "馬來文", "nan": "台語", "hak": "客語",
}


def language_name(code: str | None) -> str:
    if not code:
        return ""
    key = code.lower()
    if key in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[key]
    base = key.split("-")[0]
    return LANGUAGE_NAMES.get(base, code)


def caption_label(lang: str | None) -> str:
    """結果頁的文字來源說明，例如「YouTube 人工字幕（台灣繁體中文）」。"""
    name = language_name(lang)
    return f"YouTube 人工字幕（{name}）" if name else "YouTube 人工字幕"


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
    """YouTube 無法處理（私人影片、地區限制、下載失敗等）；message 是給使用者看的說明。"""

    def __init__(self, code: str, message: str, status: int = 422, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status  # API 回應碼：422＝影片本身的問題、502＝連線／YouTube 端暫時的問題
        # yt-dlp 原始錯誤的最後一行（已遮掉路徑），記 log 與組警告訊息用（例如取出 HTTP 狀態碼）
        self.detail = detail


# (代碼, 比對字串（小寫）, 訊息, HTTP 狀態)；由上往下第一個符合的為準
_ERROR_RULES: list[tuple[str, tuple[str, ...], str, int]] = [
    ("private", ("private video", "this video is private"),
     "這是私人影片，只有上傳者授權的帳號能看。請確認網址，或請上傳者改成公開或「不公開（知道連結的人可以看）」後再試。", 422),
    ("members_only", ("members-only", "join this channel", "channel's members", "members only"),
     "這是頻道會員限定的影片，需要會員帳號才能觀看，目前不支援。可以自行取得音檔後用上傳檔案的方式轉錄。", 422),
    ("age_restricted", ("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"),
     "這部影片有年齡限制，需要登入 YouTube 才能觀看，目前不支援。可以自行取得音檔後用上傳檔案的方式轉錄。", 422),
    ("geo_restricted", ("not made this video available in your country", "not available in your country",
                        "from your location", "geo restriction", "geo-restrict"),
     "這部影片有地區限制，在伺服器所在的地區（台灣）無法觀看。", 422),
    ("live_not_ended", ("live event will begin", "premieres in", "premiere will begin", "is_upcoming",
                        "this live event", "live stream recording is not available", "is_live", "post_live"),
     "直播或首播還沒結束（或還沒開始），YouTube 還沒有完整的影片可以下載。請等直播結束、YouTube 處理完成後再試。", 422),
    ("payment", ("requires payment", "purchase", "rental"),
     "這是付費（購買或租借）內容，無法下載。", 422),
    ("playlist_missing", ("the playlist does not exist", "playlist does not exist"),
     "播放清單不存在或是私人清單。請確認網址，或把清單改成公開／不公開後再試。", 422),
    # 只比對 YouTube 對影片本身的說明；不能用「not found」「does not exist」這種寬鬆的字，
    # 否則格式網址的 HTTP 404 也會被誤判成影片下架
    ("removed", ("has been removed", "video unavailable", "this video is unavailable", "no longer available",
                 "account associated with this video has been terminated", "this video does not exist",
                 "video does not exist", "this video isn't available anymore"),
     "影片已被下架、刪除或不存在。請確認網址是否正確。", 422),
    ("bot_check", ("not a bot", "captcha", "sign in to confirm"),
     "YouTube 暫時要求驗證（懷疑是機器人），所以拒絕下載。請過一段時間再試；如果一直發生，可能需要更新 yt-dlp 或設定 PO Token（見 README）。", 502),
    ("rate_limited", ("try again later", "http error 429", "too many requests", "rate-limited"),
     "YouTube 暫時限制了這台伺服器的請求次數（最長約 1 小時），請稍後再試。", 502),
    ("expired", ("http error 404", "http error 410"),
     "YouTube 回應找不到檔案（HTTP 404／410），通常是下載網址已過期。請重新轉錄再試一次。", 502),
    ("forbidden", ("http error 403", "403: forbidden"),
     "YouTube 拒絕下載（HTTP 403），通常是 YouTube 改版造成。請稍後再試；如果一直發生，需要更新 yt-dlp。", 502),
    ("network", ("unable to download webpage", "unable to download api page", "name resolution",
                 "temporary failure", "network is unreachable", "timed out", "connection reset",
                 "connection refused", "urlopen error", "read timed out", "getaddrinfo failed"),
     "連不上 YouTube，請確認伺服器的網路後再試。", 502),
    ("too_large", ("larger than max-filesize", "file is larger than"),
     "音訊檔超過大小上限，無法下載。", 422),
    ("no_format", ("requested format is not available", "no video formats found"),
     "找不到可以下載的音訊格式（可能是 YouTube 改版，需要更新 yt-dlp）。", 502),
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
    for code, needles, message, status in _ERROR_RULES:
        if any(n in low for n in needles):
            return YouTubeError(code, message, status, detail=detail)
    return YouTubeError("unknown", f"YouTube 下載失敗：{detail or '未知錯誤'}", 502, detail=detail)


def check_playable(summary: dict[str, Any]) -> None:
    """直播中、尚未開始、剛結束還在處理的影片不能下載；會員限定等也在這裡擋（有資訊時）。"""
    live = summary.get("live_status")
    if live == "is_live":
        raise YouTubeError("live_not_ended", "直播還在進行中，YouTube 還沒有完整的影片。請等直播結束、YouTube 處理完成後再試。")
    if live == "is_upcoming":
        raise YouTubeError("live_not_ended", "直播或首播還沒開始，沒有內容可以下載。請等播出結束後再試。")
    if live == "post_live":
        raise YouTubeError("live_not_ended", "直播剛結束，YouTube 還在處理影片，暫時無法完整下載。請過幾個小時再試。")
    availability = summary.get("availability")
    if availability == "subscriber_only":
        raise YouTubeError("members_only", _message("members_only"))
    if availability == "premium_only":
        raise YouTubeError("payment", "這是 YouTube Premium 限定內容，無法下載。")
    if availability == "needs_auth":
        raise YouTubeError("age_restricted", _message("age_restricted"))
    if availability == "private":
        raise YouTubeError("private", _message("private"))


def _message(code: str) -> str:
    for c, _, message, _ in _ERROR_RULES:
        if c == code:
            return message
    return ""


def safe_filename(title: str, fallback: str = "transcript", limit: int = 120) -> str:
    """匯出用的檔名：拿掉路徑分隔與控制字元（影片標題裡常有 /、: 之類的字元）。"""
    s = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", title or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip() or fallback

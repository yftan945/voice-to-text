"""後處理：繁體轉換（OpenCC）→ 中文旁的半形標點轉全形 → 套字典替換。

繁體轉換規則（逐段判斷，不改寫本來就是繁體的文字）：
- 字幕路線：字幕語言是 zh-TW、zh-Hant、zh-HK、zh-MO（含 zh-Hant-TW 這類變體）時完全不跑 OpenCC，
  只做全形標點與字典替換；zh-Hans、zh-CN、zh-SG 與不分區的 zh 照下面「逐段判斷」；
  其他語言（en、ja…）不跑 OpenCC（日文漢字不能被轉成繁體）。
- 模型路線與簡體字幕：逐段判斷。段落裡已經有繁體專用字（t2s 轉換後會變）就不跑 s2twp，
  只用 t2tw 把異體字（爲、裏…）統一成台灣寫法；沒有繁體專用字時跑 s2twp，
  再套「台灣慣用字保護表」把 s2twp 改寫的台灣常見寫法改回來（臺灣→台灣、瞭解→了解…）。

順序說明：先 OpenCC 再字典替換，讓使用者寫的「正字」原樣保留，不會被 OpenCC 再改寫。
誤聽同時比對原寫法與各種轉換後的寫法（s2twp＋保護表、t2tw、只轉標點），
所以不管字典裡寫簡體或繁體、段落有沒有跑 s2twp 都能命中。
"""
from __future__ import annotations

import functools
import logging
import re
from typing import Any, Callable

from .. import dictionary as dict_mod

log = logging.getLogger(__name__)

# ---- 台灣慣用字保護表 ----
# s2twp 依教育部標準字把「台」轉成「臺」、「了解」轉成「瞭解」，但台灣日常（新聞、字幕、逐字稿）
# 多半寫「台」「了解」。s2twp 跑完之後把下面這些詞改回常見寫法。
# 只換列出來的詞，不把「臺」全部換成「台」；正式名稱（KEEP_OFFICIAL）原樣保留。
# 新增時：左邊寫 s2twp 的輸出，右邊寫要改成的寫法（README 有同一份清單，改這裡時一起更新）。
TAIWAN_PREFERRED: dict[str, str] = {
    "臺灣": "台灣",
    "臺北": "台北",
    "臺中": "台中",
    "臺南": "台南",
    "臺東": "台東",
    "臺語": "台語",
    "臺幣": "台幣",
    "臺大": "台大",
    "臺詞": "台詞",
    "電視臺": "電視台",
    "平臺": "平台",
    "舞臺": "舞台",
    "後臺": "後台",
    "瞭解": "了解",
}
# 正式名稱：照 s2twp 的「臺」保留（比保護表的詞長或起點更前面，所以會優先命中）
KEEP_OFFICIAL: tuple[str, ...] = (
    "國立臺",          # 國立臺灣大學、國立臺北科技大學、國立臺中教育大學…
    "臺灣大學",
    "臺灣師範大學",
    "臺灣科技大學",
    "臺灣銀行",
    "臺灣土地銀行",
    "臺灣證券交易所",
    "臺灣高等法院",
    "新臺幣",
    "臺北市政府",
    "臺中市政府",
    "臺南市政府",
    "臺東縣政府",
)

_PROTECT_MAP: dict[str, str] = {**TAIWAN_PREFERRED, **{w: w for w in KEEP_OFFICIAL}}
# 由左到右掃描，同一個位置最長的詞優先（正式名稱會蓋過裡面較短的詞）
_PROTECT_RE = re.compile("|".join(re.escape(w) for w in sorted(_PROTECT_MAP, key=len, reverse=True)))


def protect_taiwan_terms(text: str) -> str:
    """s2twp 的輸出改回台灣慣用寫法（只用在 s2twp 轉過的文字）。"""
    return _PROTECT_RE.sub(lambda m: _PROTECT_MAP[m.group(0)], text) if text else text


# ---- OpenCC ----


@functools.lru_cache(maxsize=None)
def _opencc(config: str) -> Callable[[str], str]:
    try:
        import opencc

        try:
            conv = opencc.OpenCC(config)
        except Exception:  # noqa: BLE001 - 部分版本要寫完整設定檔名
            conv = opencc.OpenCC(f"{config}.json")
        return conv.convert
    except Exception:  # noqa: BLE001
        log.exception("OpenCC（%s）無法載入，略過繁體轉換", config)
        return lambda s: s


def s2twp(text: str) -> str:
    """OpenCC s2twp 原始輸出（不含保護表）。"""
    return _opencc("s2twp")(text) if text else text


def to_traditional(text: str) -> str:
    """簡體→台灣繁體：s2twp 再套台灣慣用字保護表。"""
    return protect_taiwan_terms(s2twp(text)) if text else text


def has_traditional(text: str) -> bool:
    """段落裡有繁體專用字（轉成簡體後會變），表示本來就是繁體，不該再跑 s2twp。"""
    return bool(text) and _opencc("t2s")(text) != text


# 轉換模式：auto＝逐段判斷（模型路線、簡體或不分區的中文字幕）；none＝不跑 OpenCC（繁體字幕、非中文字幕）
AUTO = "auto"
NONE = "none"

_TRADITIONAL_CAPTIONS = ("zh-tw", "zh-hant", "zh-hk", "zh-mo")
_AUTO_CAPTIONS = ("zh-hans", "zh-cn", "zh-sg")


def _lang_matches(lang: str, base: str) -> bool:
    return lang == base or lang.startswith(base + "-")


def mode_for_captions(caption_lang: str | None) -> str:
    """字幕路線的轉換模式（依字幕語言）。"""
    lang = (caption_lang or "").strip().lower().replace("_", "-")
    if any(_lang_matches(lang, b) for b in _TRADITIONAL_CAPTIONS):
        return NONE
    if lang == "zh" or any(_lang_matches(lang, b) for b in _AUTO_CAPTIONS):
        return AUTO
    return NONE


def convert(text: str, mode: str = AUTO) -> str:
    """依模式轉換一段文字（不含標點）。"""
    if not text or mode == NONE:
        return text
    if has_traditional(text):
        return _opencc("t2tw")(text)
    return to_traditional(text)


_CJK = "㐀-鿿豈-﫿"
_FULLWIDTH = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；", ".": "。"}
# 刪節號：連續兩個以上的點（或「…」）當成一個整體，緊貼中文字時換成「……」
_ELLIPSIS_RE = re.compile(rf"(?<=[{_CJK}])\s*(?:\.{{2,}}|…+)|(?:\.{{2,}}|…+)\s*(?=[{_CJK}])")
# 緊貼中文字的半形標點（前一個或後一個字是中文）；英文句子與數字（3.14、10:30）不受影響
# 句點只處理單獨一個（連續的點是刪節號，上面已處理）
_HALF_PUNCT_RE = re.compile(rf"(?<=[{_CJK}])(?:[,?!:;]|\.(?!\.))|[,?!:;](?=[{_CJK}])")


def fullwidth_punct(text: str) -> str:
    """台灣中文習慣用全形標點：Whisper 常輸出半形逗號，在中文字旁邊時換成全形。"""
    out = _ELLIPSIS_RE.sub("……", text)
    out = _HALF_PUNCT_RE.sub(lambda m: _FULLWIDTH[m.group(0)], out)
    # 全形標點後面多出來的空白一併去掉
    return re.sub(r"([，？！：；。…])[ \t]+", r"\1", out)


def normalize_text(text: str, mode: str = AUTO) -> str:
    """辨識文字的正規化：繁體轉換（依模式）＋中文旁標點轉全形。"""
    return fullwidth_punct(convert(text, mode))


def wrong_variants(wrong: str) -> set[str]:
    """字典誤聽要比對的寫法：段落可能跑 s2twp（＋保護表）、t2tw 或完全不轉，各種結果都要能命中。"""
    return {
        fullwidth_punct(wrong),
        fullwidth_punct(to_traditional(wrong)),
        fullwidth_punct(_opencc("t2tw")(wrong)),
    }


def build_processor(d: dict_mod.Dictionary, mode: str = AUTO) -> Callable[[str], str]:
    # 誤聽也經過同樣的轉換（繁體＋全形標點），例如誤聽寫「等等...」也能命中「等等……」
    replacer = dict_mod.Replacer(d.rules, normalize=wrong_variants)

    def process(text: str) -> str:
        return replacer(normalize_text(text, mode))

    return process


def apply_to_segments(segments: list[dict[str, Any]], d: dict_mod.Dictionary, mode: str = AUTO) -> None:
    """mode：模型路線用 AUTO；字幕路線用 mode_for_captions(字幕語言)。"""
    process = build_processor(d, mode)
    for seg in segments:
        seg["text"] = process(seg.get("text", ""))

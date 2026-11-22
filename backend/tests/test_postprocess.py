"""後處理順序：繁體轉換（OpenCC 逐段判斷＋台灣慣用字保護表）→ 中文旁標點轉全形 → 字典替換。"""
from __future__ import annotations

import pytest

from app import dictionary as dict_mod
from app.pipeline import postprocess as pp
from app.pipeline.postprocess import apply_to_segments, build_processor, fullwidth_punct, to_traditional


def process(text, dictionary="", mode=pp.AUTO):
    return build_processor(dict_mod.parse(dictionary), mode)(text)


def test_opencc_precondition_changes_tai():
    """前提：OpenCC s2twp 本身會把「台灣」改成「臺灣」；套保護表之後改回「台灣」。"""
    assert pp.s2twp("台灣") == "臺灣"
    assert to_traditional("台灣") == "台灣"


def test_user_correct_word_with_tai_is_not_rewritten_by_opencc():
    """字典正字含「台」：先 OpenCC 再替換，所以使用者寫的「台灣」原樣保留，不會變成「臺灣」。"""
    assert process("我住在台湾", "台灣 = 台湾") == "我住在台灣"


def test_wrong_written_in_traditional_matches_simplified_asr_output():
    """誤聽寫繁體（使用者常這樣打），ASR 輸出簡體也要命中。"""
    # 「台機電」經 OpenCC 會變「臺機電」（不在保護表裡），只比對原寫法會漏掉
    assert to_traditional("台机电") == "臺機電"
    assert process("台机电的股价", "台積電 = 台機電") == "台積電的股價"


def test_wrong_written_in_traditional_matches_traditional_asr_output():
    """誤聽寫繁體，ASR 輸出繁體也命中。"""
    assert process("台機電的股價", "台積電 = 台機電") == "台積電的股價"


def test_wrong_written_in_simplified_matches():
    """誤聽寫簡體也能命中（ASR 文字先轉成繁體，誤聽同時比對轉換後的寫法）。"""
    assert process("用库伯内题斯部署", "Kubernetes = 库伯内题斯") == "用Kubernetes部署"


def test_correct_word_using_mainland_term_is_kept():
    """正字是 OpenCC 會改寫的詞（例如「視頻」會被轉成「影片」）也原樣保留。"""
    assert to_traditional("視頻") == "影片"
    assert process("这个时评很好看", "視頻 = 時評") == "這個視頻很好看"


def test_halfwidth_comma_before_dictionary():
    """標點轉全形與字典替換同時作用：中文旁的半形逗號轉全形，字典照常替換。"""
    assert process("人工只能,很好", "人工智慧 = 人工只能") == "人工智慧，很好"


def test_apply_to_segments_processes_every_segment():
    segs = [{"text": "软件,很好"}, {"text": "Hello, world."}, {"text": ""}]
    apply_to_segments(segs, dict_mod.parse(""))
    assert [s["text"] for s in segs] == ["軟體，很好", "Hello, world.", ""]


@pytest.mark.parametrize("text, expected", [
    ("你好,世界", "你好，世界"),
    ("好的.我知道了", "好的。我知道了"),
    ("真的嗎?", "真的嗎？"),
    ("注意:明天開會;記得帶筆!", "注意：明天開會；記得帶筆！"),
    ("你好, 世界", "你好，世界"),  # 全形標點後多出來的空白去掉
])
def test_halfwidth_punct_next_to_chinese_becomes_fullwidth(text, expected):
    assert fullwidth_punct(text) == expected


@pytest.mark.parametrize("text", [
    "他說 Hello, how are you? 然後走了",
    "我們用 Node.js 開發",
    "版本 v1.2.3 上線了",
    "I think so. OK, let's go!",
    "Pi is 3.14, at 10:30",
    "email 寄到 a.b@example.com 就好",
])
def test_english_punct_is_untouched(text):
    """英文句子、網址、版本號、數字裡的標點不轉全形。"""
    assert fullwidth_punct(text) == text


def test_time_inside_chinese_keeps_colon_but_comma_converted():
    """10:30 的冒號在數字之間不轉，緊接中文的逗號才轉。"""
    assert fullwidth_punct("時間是10:30,好嗎") == "時間是10:30，好嗎"


def test_english_sentence_inside_chinese_after_processing():
    """完整後處理（含 OpenCC）也不會動到英文句子裡的標點與大小寫。"""
    assert process("我们说 Hello, world. 然后继续") == "我們說 Hello, world. 然後繼續"


# ---- B4：刪節號 ----


@pytest.mark.parametrize("text, expected", [
    ("等等...", "等等……"),
    ("等等... 然後呢", "等等……然後呢"),
    ("好...我知道", "好……我知道"),
    ("嗯…對", "嗯……對"),
    ("他說……", "他說……"),
    ("wait... ok", "wait... ok"),  # 英文的刪節號不動
])
def test_ellipsis_is_one_unit(text, expected):
    assert fullwidth_punct(text) == expected


def test_dictionary_wrong_with_halfwidth_punct_still_matches():
    """誤聽也經過同一套全形標點轉換：字典寫「等等...」能命中辨識結果的「等等……」。"""
    assert process("我說等等...好", "等一下 = 等等...") == "我說等一下好"
    assert process("真的嗎?好", "是嗎 = 真的嗎?") == "是嗎好"


# ---- 繁體轉換規則（OpenCC 不改寫台灣繁體） ----


@pytest.mark.parametrize("simplified, expected", [
    # s2twp 會改寫的台灣慣用字：保護表改回來
    ("我住在台湾", "我住在台灣"),
    ("台北很热闹", "台北很熱鬧"),
    ("台中的天气", "台中的天氣"),
    ("台南小吃", "台南小吃"),
    ("台东很美", "台東很美"),
    ("他会说台语", "他會說台語"),
    ("这家电视台", "這家電視台"),
    ("我了解了", "我了解了"),
    ("在舞台上，这个平台的后台", "在舞台上，這個平台的後台"),
    # 正式名稱保留「臺」
    ("国立台湾大学", "國立臺灣大學"),
    ("台湾银行", "臺灣銀行"),
    ("我在台湾银行上班，住在台湾", "我在臺灣銀行上班，住在台灣"),
    ("新台币一百元", "新臺幣一百元"),
    # 不在表上的「臺」不會被全域換成「台」（s2twp 的「一臺電腦」照原樣）
    ("一台电脑", "一臺電腦"),
])
def test_simplified_segment_runs_s2twp_then_taiwan_preferred(simplified, expected):
    assert process(simplified) == expected


@pytest.mark.parametrize("text", [
    "我住在台灣，常看電視台的節目",
    "國立臺灣大學與臺灣銀行",
    "臺灣的正式寫法也保留",          # 本來就寫「臺」不改成「台」
    "我瞭解你的意思",                # 本來就寫「瞭解」也不改
    "這個軟件很好用",                # 有繁體字：不跑 s2twp，「軟件」不會被改成「軟體」
])
def test_traditional_segment_is_not_rewritten(text):
    """模型輸出已經是繁體（有繁體專用字）時不跑 s2twp。"""
    assert pp.has_traditional(text)
    assert process(text) == text


def test_traditional_segment_uses_t2tw_for_variants():
    """已經是繁體的段落只用 t2tw 統一異體字（爲→為、裏→裡）。"""
    assert process("爲什麼在這裏") == "為什麼在這裡"


def test_ambiguous_segment_without_traditional_chars_runs_s2twp():
    """「台北」「了解」這類簡繁同形的字沒有繁體專用字：跑 s2twp，保護表把結果改回台灣寫法。"""
    assert not pp.has_traditional("台北了解")
    assert process("台北了解") == "台北了解"


def test_segments_are_judged_one_by_one():
    segs = [{"text": "我住在台湾,软件很好"}, {"text": "我住在台灣,軟件很好"}, {"text": "了解"}]
    apply_to_segments(segs, dict_mod.parse(""))
    assert [s["text"] for s in segs] == ["我住在台灣，軟體很好", "我住在台灣，軟件很好", "了解"]


@pytest.mark.parametrize("lang, mode", [
    ("zh-TW", pp.NONE), ("zh-Hant", pp.NONE), ("zh-HK", pp.NONE), ("zh-MO", pp.NONE),
    ("zh-Hant-TW", pp.NONE), ("zh-tw", pp.NONE),
    ("zh-Hans", pp.AUTO), ("zh-CN", pp.AUTO), ("zh-SG", pp.AUTO), ("zh-Hans-CN", pp.AUTO), ("zh", pp.AUTO),
    ("en", pp.NONE), ("en-US", pp.NONE), ("ja", pp.NONE), (None, pp.NONE),
])
def test_mode_for_captions(lang, mode):
    assert pp.mode_for_captions(lang) == mode


def test_traditional_captions_skip_opencc_but_keep_punct_and_dictionary():
    """zh-TW 等繁體字幕：不跑 OpenCC（連簡體字也不轉），只做全形標點與字典替換。"""
    d = "人工智慧 = 人工只能\n台積電 = 台積店"
    assert process("台北,了解,电视台", mode=pp.NONE) == "台北，了解，电视台"
    assert process("人工只能,台積店", d, mode=pp.NONE) == "人工智慧，台積電"


def test_simplified_captions_are_converted():
    segs = [{"text": "我了解台北的电视台"}, {"text": "软件"}]
    apply_to_segments(segs, dict_mod.parse(""), pp.mode_for_captions("zh-Hans"))
    assert [s["text"] for s in segs] == ["我了解台北的電視台", "軟體"]


def test_dictionary_runs_after_opencc_and_matches_all_variants():
    """字典在 OpenCC 之後；誤聽同時比對原寫法、s2twp＋保護表、t2tw 的寫法。"""
    # 誤聽寫簡體、文字是簡體（跑 s2twp）
    assert process("用库伯内题斯部署", "Kubernetes = 库伯内题斯") == "用Kubernetes部署"
    # 誤聽寫異體字、文字是繁體（跑 t2tw，「爲」變「為」）
    assert process("爲什麼不行", "為何 = 爲什麼") == "為何不行"
    # 誤聽寫「臺北」以外的寫法：文字經保護表變成「台北」，誤聽「台北捷運」照樣命中
    assert process("台北捷运很方便", "北捷 = 台北捷運") == "北捷很方便"
    # 正字含「臺」：字典在保護表之後，正字原樣保留
    assert process("我在台湾大學念書", "臺灣大學 = 台湾大學") == "我在臺灣大學念書"


def test_wrong_variants():
    v = pp.wrong_variants("台湾")
    assert {"台湾", "台灣"} <= v

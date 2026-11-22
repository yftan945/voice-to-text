"""assign_speakers 整份串接後平滑／對齊邊界的補充測試：
輸出不變式（固定種子的隨機輸入）、3 小時規模的執行時間、多層夾擊與邊界輸入、字幕逐段呼叫路徑。"""
from __future__ import annotations

import random
import time

import pytest

from app.pipeline import captions
from app.pipeline.assign import (
    align_boundaries,
    assign_speakers,
    fill_word_times,
    smooth_labels,
)


def w(text, s, e):
    return {"text": text, "start": s, "end": e}


def _seg(words, text=""):
    return {"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}


def _nonspace(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


# ---------------------------------------------------------------------------
# 隨機輸入產生器（固定種子，結果可重現）
# ---------------------------------------------------------------------------

CHARS = "我們來看一下這個功能好對啊嗯"
EN = ["Hello", " world", " open", " source", " OK"]
SPEAKERS = ["A", "B", "C"]


def _random_case(seed: int, messy: bool = False):
    """產生 (asr_segments, turns)。
    messy=False：字的時間單調遞增（可能有零長度字、空白字、缺時間的字）。
    messy=True：另外摻入時間重疊或倒序的字。"""
    r = random.Random(seed)
    segs = []
    t = 0.0
    for _ in range(r.randint(1, 8)):
        words = []
        for _ in range(r.randint(1, 15)):
            kind = r.random()
            if kind < 0.1:
                words.append({"text": " "})  # 空白字（中英混合時 restore_spaces 會插入），通常沒有時間
                continue
            text = r.choice(EN) if kind < 0.3 else r.choice(CHARS)
            if kind > 0.92:
                words.append({"text": text})  # 缺時間 → 內插
                continue
            dur = 0.0 if r.random() < 0.08 else r.uniform(0.05, 0.6)  # 偶爾零長度
            t += r.choice([0.0, 0.02, 0.1, 0.18, 0.3, 0.8])  # 字間停頓
            s = t
            if messy and r.random() < 0.2:
                s = max(0.0, t - r.uniform(0.1, 1.0))  # 時間重疊或倒退
            words.append(w(text, round(s, 3), round(s + dur, 3)))
            t = max(t, s + dur)
        seg_start = next((x["start"] for x in words if x.get("start") is not None), t)
        t += r.uniform(0.0, 1.5)
        seg = {"start": seg_start, "end": max(t, seg_start), "text": "", "words": words}
        if r.random() < 0.1:  # 偶爾整段沒有逐字資料，只有段落文字
            seg["text"] = "".join(x["text"] for x in words)
            seg["words"] = []
        segs.append(seg)
    total = t + 1.0
    # 互不重疊、涵蓋整份時間軸的講者區段（相鄰區段講者不同）
    cuts = sorted(r.uniform(0, total) for _ in range(r.randint(0, 12)))
    turns, prev, spk = [], 0.0, r.choice(SPEAKERS)
    for c in cuts + [total + 5.0]:
        turns.append((prev, c, spk))
        prev = c
        spk = r.choice([x for x in SPEAKERS if x != spk])
    return segs, turns


def _input_text(segs) -> str:
    out = []
    for seg in segs:
        out.append("".join(str(x["text"]) for x in fill_word_times(seg)))
    return _nonspace("".join(out))


def _owner_segments(segs, out):
    """依非空白字元位置，把每個輸出段落對回它所在的 ASR 段落索引；
    輸出段落跨越兩個 ASR 段落時回傳 None 於該位置。"""
    bounds = []  # 每個 ASR 段落的非空白字元區間 [lo, hi)
    pos = 0
    for seg in segs:
        n = len(_nonspace("".join(str(x["text"]) for x in fill_word_times(seg))))
        bounds.append((pos, pos + n))
        pos += n
    owners, pos = [], 0
    for o in out:
        n = len(_nonspace(o["text"]))
        lo, hi = pos, pos + n
        owner = None
        for idx, (a, b) in enumerate(bounds):
            if a <= lo and hi <= b and a < b:
                owner = idx
                break
        owners.append(owner)
        pos = hi
    return owners


SEEDS = list(range(300))


@pytest.mark.parametrize("seed", SEEDS)
def test_invariants_on_random_transcripts(seed):
    """隨機逐字稿的輸出不變式：文字不遺失、id 連續、start <= end、講者不為 None、
    沒有空文字段落、輸出段落不跨 ASR 段落、同一 ASR 段落內相鄰段落講者不同。"""
    segs, turns = _random_case(seed)
    out = assign_speakers(segs, turns)

    # 所有非空白字依序串起來等於輸入
    assert _nonspace("".join(o["text"] for o in out)) == _input_text(segs)
    # id 從 0 連續
    assert [o["id"] for o in out] == list(range(len(out)))
    for o in out:
        assert o["text"].strip(), o  # 不產生空文字段落
        assert o["start"] <= o["end"], o  # 時間單調的輸入 → start <= end
        assert o["speaker"] in SPEAKERS, o  # turns 涵蓋整份時間軸 → 一定有講者
    # 輸出段落不跨越 ASR 段落，同一 ASR 段落內相鄰的輸出段落講者一定不同（否則不該切開）
    owners = _owner_segments(segs, out)
    assert None not in owners, (owners, out)
    for i in range(1, len(out)):
        if owners[i] == owners[i - 1]:
            assert out[i]["speaker"] != out[i - 1]["speaker"], (out[i - 1], out[i])
    # 段落依時間順序（ASR 段落順序）輸出
    assert owners == sorted(owners)


@pytest.mark.parametrize("seed", SEEDS[:100])
def test_invariants_without_turns(seed):
    """沒有講者區段時：每個有文字的 ASR 段落剛好一個輸出段落、講者 None、文字不遺失。"""
    segs, _ = _random_case(seed)
    out = assign_speakers(segs, [])
    assert _nonspace("".join(o["text"] for o in out)) == _input_text(segs)
    assert all(o["speaker"] is None for o in out)
    non_empty = [s for s in segs if _nonspace("".join(str(x["text"]) for x in fill_word_times(s)))]
    assert len(out) == len(non_empty)
    assert [o["id"] for o in out] == list(range(len(out)))


@pytest.mark.parametrize("seed", SEEDS[:150])
def test_messy_times_do_not_crash_or_lose_text(seed):
    """時間重疊、倒序、零長度的字：不丟例外、文字不遺失、id 連續、講者都有值。"""
    segs, turns = _random_case(seed, messy=True)
    out = assign_speakers(segs, turns)
    assert _nonspace("".join(o["text"] for o in out)) == _input_text(segs)
    assert [o["id"] for o in out] == list(range(len(out)))
    assert all(o["text"].strip() and o["speaker"] in SPEAKERS for o in out)


@pytest.mark.parametrize("seed", SEEDS[:100])
def test_smooth_and_align_keep_label_set_and_length(seed):
    """smooth_labels／align_boundaries 只改標籤、不改長度，也不會憑空產生新的講者。"""
    segs, turns = _random_case(seed)
    words = [x for s in segs for x in fill_word_times(s)]
    if not words:
        return
    r = random.Random(seed)
    labels = [r.choice(SPEAKERS) for _ in words]
    sm = smooth_labels(words, labels)
    al = align_boundaries(words, sm)
    assert len(sm) == len(al) == len(words)
    assert set(al) <= set(sm) <= set(labels)
    # 對齊邊界只移動相鄰 run 的分界：run 的講者順序（壓縮連續相同者）不變
    def order(ls):
        return [ls[0]] + [ls[i] for i in range(1, len(ls)) if ls[i] != ls[i - 1]]
    assert order(al) == order(sm)
    # 傳入的 list 不被就地修改
    labels_copy = list(labels)
    smooth_labels(words, labels)
    align_boundaries(words, labels)
    assert labels == labels_copy


# ---------------------------------------------------------------------------
# 3 小時規模的執行時間
# ---------------------------------------------------------------------------


def _long_transcript(n_words=30000, n_changes=300, n_flips=0, seed=7):
    """約 3 小時（每字 0.3 秒、每 25 字一個 ASR 段落、段落間 0.3 秒停頓）。
    n_changes 次真正換人，另外插入 n_flips 個 0.5 秒的誤判（講者 X）。"""
    r = random.Random(seed)
    words, t = [], 0.0
    for i in range(n_words):
        words.append(w("字", round(t, 3), round(t + 0.3, 3)))
        t += 0.3 + (0.3 if i % 25 == 24 else 0.02)
    total = t
    cuts = sorted(r.uniform(0, total) for _ in range(n_changes))
    base, prev = [], 0.0
    for k, c in enumerate(cuts + [total + 1.0]):
        base.append((prev, c, "S%d" % (k % 3)))
        prev = c
    flips = sorted((x, x + 0.5) for x in (r.uniform(0, total) for _ in range(n_flips)))
    turns = []
    for a, b, spk in base:
        cur = a
        for fa, fb in flips:
            if cur < fa and fb < b:
                turns.append((cur, fa, spk))
                turns.append((fa, fb, "X"))
                cur = fb
        turns.append((cur, b, spk))
    segs = [_seg(words[i:i + 25]) for i in range(0, n_words, 25)]
    return segs, turns


def test_three_hour_transcript_runs_within_time_limit():
    """3 萬字、300 次換人、50 個短誤判（約 400 個講者區段）的整份處理要在 2 秒內完成。"""
    segs, turns = _long_transcript(n_changes=300, n_flips=50)
    t0 = time.perf_counter()
    out = assign_speakers(segs, turns)
    elapsed = time.perf_counter() - t0
    # 文字不遺失；大部分 0.5 秒誤判被吸收（靠近真正換人處的不一定被同一人夾住）
    assert sum(len(o["text"]) for o in out) == 30000
    assert sum(o["speaker"] == "X" for o in out) < 10
    assert elapsed < 2.0, f"assign_speakers 花了 {elapsed:.2f} 秒"


def _smooth_align_seconds(n_words: int, period: int) -> float:  # period 個字約 0.32*period 秒，需 >= 3 秒鄰居才吸收
    words = [w("字", round(0.32 * i, 3), round(0.32 * i + 0.3, 3)) for i in range(n_words)]
    labels = ["X" if i % period == period // 2 else ("A" if (i // 3000) % 2 else "B")
              for i in range(n_words)]
    best = float("inf")
    for _ in range(2):  # 取兩次中較快的一次，降低機器雜訊
        t0 = time.perf_counter()
        out = align_boundaries(words, smooth_labels(words, labels))
        best = min(best, time.perf_counter() - t0)
        assert "X" not in out
    return best


def test_smoothing_many_short_flips_scales_linearly():
    """平滑＋對齊對「誤判數隨長度等比增加」的輸入要接近線性：
    字數與誤判數都加倍（1.5 萬字／1500 個 → 3 萬字／3000 個），時間不應超過 3 倍
    （線性約 2 倍；每吸收一個 run 就重算整條序列的 runs 是 O(誤判數 × 字數)，約 4 倍）。"""
    small = _smooth_align_seconds(15000, 15)
    large = _smooth_align_seconds(30000, 15)
    assert large < 0.2 or large / small < 3.0, f"{small:.2f}s -> {large:.2f}s（{large / small:.1f} 倍）"


# ---------------------------------------------------------------------------
# 多層夾擊與特殊排列
# ---------------------------------------------------------------------------


def test_nested_alternating_short_runs_collapse_to_outer_speaker():
    # A(長 >= 3 秒) B A B A(長)，中間三個 run 各 0.3 秒：每個 B 都有一側是長 A → 吸收，結果全是 A
    words = [w("a", 0, 4.0), w("b", 4.0, 4.3), w("c", 4.3, 4.6), w("d", 4.6, 4.9), w("e", 4.9, 9.0)]
    assert smooth_labels(words, ["A", "B", "A", "B", "A"]) == ["A"] * 5


def test_alternating_runs_with_short_neighbors_are_kept():
    # 兩側都是短 run 的交替（快速一問一答）不可被合併
    words = [w("a", 0, 1.0), w("b", 1.0, 1.8), w("c", 1.8, 2.6), w("d", 2.6, 3.4), w("e", 3.4, 4.5)]
    labels = ["A", "B", "A", "B", "A"]
    assert smooth_labels(words, labels) == labels


def test_nested_alternating_runs_end_to_end():
    # 外側 A 各 3 秒以上，中間 B／A／B 各 0.2 秒快速切換 → 兩個 B 都被吸收成同一句
    words = [w(c, 0.6 * i, 0.6 * (i + 1)) for i, c in enumerate("我們先看今天的議程")]  # 0~5.4
    words += [w(c, 5.4 + 0.2 * i, 5.4 + 0.2 * (i + 1)) for i, c in enumerate("好嗎")]
    words += [w(c, 5.8 + 0.6 * i, 5.8 + 0.6 * (i + 1)) for i, c in enumerate("那我們開始吧")]
    turns = [(0, 5.4, "A"), (5.4, 5.6, "B"), (5.6, 5.8, "A"), (5.8, 9.4, "B")]
    # 後面 B 是真換人（不是被夾住）
    out = assign_speakers([_seg(words)], turns)
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "我們先看今天的議程好嗎"), ("B", "那我們開始吧")]


def test_two_different_short_runs_between_same_speaker_are_kept():
    # A B C A：B、C 各自前後講者不同（不是被同一人夾住）→ 依規則不吸收
    words = [w("a", 0, 2.0), w("b", 2.0, 2.3), w("c", 2.3, 2.6), w("d", 2.6, 5.0)]
    labels = ["A", "B", "C", "A"]
    assert smooth_labels(words, labels) == labels


def test_single_speaker_transcript_is_one_segment_per_asr_segment():
    segs = [_seg([w("第", 0, .3), w("一", .3, .6)]), _seg([w("第", 1, 1.3), w("二", 1.3, 1.6)])]
    out = assign_speakers(segs, [(0, 5, "A")])
    assert [(o["id"], o["speaker"], o["text"]) for o in out] == [(0, "A", "第一"), (1, "A", "第二")]


def test_empty_turns_list_behaves_like_no_diarization():
    segs = [_seg([w("嗨", 0, .5)]), _seg([w("你", 1, 1.3), w("好", 1.3, 1.6)])]
    assert assign_speakers(segs, []) == assign_speakers(segs, None)
    assert [o["speaker"] for o in assign_speakers(segs, [])] == [None, None]


def test_empty_input_returns_empty_list():
    assert assign_speakers([], [(0, 1, "A")]) == []
    assert assign_speakers([{"start": 0, "end": 1, "text": "  ", "words": []}], [(0, 1, "A")]) == []
    assert assign_speakers([{"start": 0, "end": 1, "text": ""}], [(0, 1, "A")]) == []


def test_segment_with_all_words_missing_times_is_assigned_and_split():
    # 整段沒有時間戳 → 平均分配，仍可依講者切開；內插時間不參與邊界對齊
    seg = {"start": 0, "end": 4, "text": "", "words": [{"text": c} for c in "甲乙丙丁"]}
    out = assign_speakers([seg], [(0, 2, "A"), (2, 4, "B")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "甲乙"), ("B", "丙丁")]


def test_zero_length_words_use_midpoint_and_keep_text():
    words = [w("a", 1.0, 1.0), w("b", 1.0, 1.0), w("c", 4.0, 4.0), w("d", 4.0, 4.0)]
    out = assign_speakers([_seg(words)], [(0, 2, "A"), (2, 6, "B")])
    # 前後各只有零秒長的 run，B 不是被同一講者夾住 → 保留換人
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "ab"), ("B", "cd")]
    assert all(o["start"] <= o["end"] for o in out)


def test_mixed_chinese_english_with_spaces_across_speaker_change():
    words = [w("我用", 0, .5), {"text": " "}, w("Docker", .6, 1.2), {"text": " "}, w("部署", 1.3, 1.8),
             w("那", 3.0, 3.3), {"text": " "}, w("OK", 3.4, 3.8)]
    out = assign_speakers([_seg(words)], [(0, 2.4, "A"), (2.4, 6, "B")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "我用 Docker 部署"), ("B", "那 OK")]


def test_boundary_alignment_does_not_split_off_a_whitespace_only_segment():
    # 空白字之後的停頓最大：邊界不能落在空白字前面，造成只剩空白的段落或文字遺失
    words = [w("Hi", 0, .3), w(" there", .3, .6), {"text": " "}, w("yes", 1.2, 1.5), w(" sure", 1.5, 1.8)]
    # 「 there」被判給 B（標籤早了一個字），0.6 秒停頓在空白字之後、「yes」之前
    out = assign_speakers([_seg(words)], [(0, .35, "A"), (.35, 4, "B")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "Hi there"), ("B", "yes sure")]


def test_cross_segment_short_run_after_long_silence_is_kept():
    """時長 <= 1.5 秒的 run 前後停頓 >= 1.0 秒（長靜默隔開的短回答）要保留。"""
    segs = [_seg([w("你", 0, 1.5), w("要", 1.5, 3.0), w("嗎", 3.0, 4.5)]),
            _seg([w("要", 10.0, 10.4)]),
            _seg([w("好", 20.0, 21.5), w("的", 21.5, 23.0)])]
    turns = [(0, 5, "A"), (9.5, 11, "B"), (19.5, 25, "A")]
    out = assign_speakers(segs, turns)
    assert [o["speaker"] for o in out] == ["A", "B", "A"]


def test_cross_segment_short_run_with_short_pauses_is_absorbed():
    segs = [_seg([w("你", 0, 1.5), w("要", 1.5, 3.0), w("嗎", 3.0, 4.5)]),
            _seg([w("要", 4.9, 5.3)]),
            _seg([w("好", 5.7, 7.2), w("的", 7.2, 8.7)])]
    turns = [(0, 4.8, "A"), (4.8, 5.5, "B"), (5.5, 10, "A")]
    out = assign_speakers(segs, turns)
    assert [o["speaker"] for o in out] == ["A", "A", "A"]


# ---------------------------------------------------------------------------
# 回退：沒有對到任何講者區段
# ---------------------------------------------------------------------------


def test_middle_segment_without_turn_inherits_previous_speaker():
    segs = [_seg([w("a", 0, 1)]), _seg([w("b", 30, 31)]), _seg([w("c", 60, 61)])]
    out = assign_speakers(segs, [(0, 1, "A"), (60, 61, "B")])
    assert [o["speaker"] for o in out] == ["A", "A", "B"]


def test_first_segment_without_any_turn_gets_a_speaker():
    """第一個 ASR 段落離所有講者區段都超過 2 秒、也沒有上一段可沿用：
    有 turns 時輸出不應出現 speaker None（應回退到之後第一個有講者的段落）。"""
    segs = [_seg([w("a", 0, .5), w("b", .5, 1.0)]), _seg([w("c", 10, 11)])]
    out = assign_speakers(segs, [(10, 12, "A")])
    assert [o["text"] for o in out] == ["ab", "c"]
    assert [o["speaker"] for o in out] == ["A", "A"]


def test_leading_unmatched_words_inside_first_segment_use_next_word():
    # 同一段內段首的字沒有對到講者 → 沿用後一個字的講者，不產生 None 段落
    words = [w("a", 0, .5), w("b", 5.0, 5.5), w("c", 5.5, 6.0)]
    out = assign_speakers([_seg(words)], [(5, 7, "A")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "abc")]


# ---------------------------------------------------------------------------
# 字幕：assign_caption_speakers 逐段呼叫 assign_speakers
# ---------------------------------------------------------------------------


def test_caption_cue_with_words_smooths_short_flip_within_cue():
    cue = {"start": 0.0, "end": 7.0, "text": "今天嗯開會",
           "words": [w("今", 0, 1.6), w("天", 1.6, 3.2), w("嗯", 3.2, 3.5), w("開", 3.5, 5.0), w("會", 5.0, 6.5)]}
    out = captions.assign_caption_speakers([cue], [(0, 3.2, "A"), (3.2, 3.5, "B"), (3.5, 7, "A")])
    assert out == [{"start": 0.0, "end": 6.5, "speaker": "A", "text": "今天嗯開會", "id": 0}]


def test_caption_leading_cue_without_turn_gets_first_known_speaker():
    cues = [{"start": 0.0, "end": 1.0, "text": "第一句"},
            {"start": 30.0, "end": 31.0, "text": "遠方"}]
    out = captions.assign_caption_speakers(cues, [(30, 32, "A")])
    assert [o["speaker"] for o in out] == ["A", "A"]


def test_caption_cue_without_matching_turn_inherits_previous_cue_speaker():
    cues = [{"start": 0.0, "end": 1.0, "text": "第一句"},
            {"start": 30.0, "end": 31.0, "text": "遠方", "words": [w("遠", 30, 30.5), w("方", 30.5, 31)]}]
    out = captions.assign_caption_speakers(cues, [(0, 1, "A"), (60, 61, "B")])
    assert [(o["id"], o["speaker"], o["text"]) for o in out] == [(0, "A", "第一句"), (1, "A", "遠方")]


def test_caption_cues_mixing_word_and_plain_cues_keep_ids_and_order():
    cues = [{"start": 0.0, "end": 2.0, "text": "你好我是",
             "words": [w("你好", 0, .9), w("我是", 1.1, 2.0)]},
            {"start": 2.5, "end": 3.0, "text": "純文字"}]
    out = captions.assign_caption_speakers(cues, [(0, 1, "A"), (1, 2.2, "B"), (2.2, 4, "A")])
    assert [(o["id"], o["speaker"], o["text"]) for o in out] == [
        (0, "A", "你好"), (1, "B", "我是"), (2, "A", "純文字")]

import pytest

from app.pipeline.assign import (
    align_boundaries,
    assign_speakers,
    fill_word_times,
    smooth_labels,
    speaker_for,
)


def w(text, s, e):
    return {"text": text, "start": s, "end": e}


def test_split_at_speaker_change():
    segs = [{"start": 0, "end": 4, "text": "你好嗎我很好", "words": [
        w("你", 0.0, 0.3), w("好", 0.3, 0.6), w("嗎", 0.6, 0.9),
        w("我", 2.0, 2.3), w("很", 2.3, 2.6), w("好", 2.6, 2.9),
    ]}]
    turns = [(0.0, 1.0, "A"), (1.8, 3.0, "B")]
    out = assign_speakers(segs, turns)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "你好嗎"), ("B", "我很好")]
    assert out[0]["start"] == 0.0 and out[0]["end"] == 0.9
    assert out[1]["start"] == 2.0 and out[1]["end"] == 2.9
    assert [s["id"] for s in out] == [0, 1]


def test_asr_segment_boundary_is_kept_even_for_same_speaker():
    segs = [
        {"start": 0, "end": 1, "text": "第一句", "words": [w("第", 0, .3), w("一", .3, .6), w("句", .6, .9)]},
        {"start": 1, "end": 2, "text": "第二句", "words": [w("第", 1, 1.3), w("二", 1.3, 1.6), w("句", 1.6, 1.9)]},
    ]
    out = assign_speakers(segs, [(0, 2, "A")])
    assert [s["text"] for s in out] == ["第一句", "第二句"]


def test_short_flip_is_smoothed():
    # 「嗯」被誤判成 B，但前後都是 A（至少一側講了 3 秒以上）且很短 → 改回 A
    words = [w("今", 0, 1.6), w("天", 1.6, 3.2), w("嗯", 3.2, 3.5), w("開", 3.5, 5.0), w("會", 5.0, 6.5)]
    turns = [(0, 3.2, "A"), (3.2, 3.5, "B"), (3.5, 7, "A")]
    out = assign_speakers([{"start": 0, "end": 7, "text": "", "words": words}], turns)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "今天嗯開會")]


def test_long_interjection_is_not_smoothed():
    # B 講了 3 秒（超過連續講話的 2.5 秒上限）→ 保留
    words = [w("今", 0, .2), w("天", .2, .4), w("對", .4, 1.9), w("對", 1.9, 3.4), w("開", 3.4, 3.6)]
    turns = [(0, .4, "A"), (.4, 3.4, "B"), (3.4, 4, "A")]
    out = assign_speakers([{"start": 0, "end": 4, "text": "", "words": words}], turns)
    assert [s["speaker"] for s in out] == ["A", "B", "A"]


def test_missing_word_times_are_interpolated():
    seg = {"start": 0, "end": 3, "text": "", "words": [
        w("a", 0.0, 1.0), {"text": "1", "start": None, "end": None}, {"text": "2"}, w("b", 2.0, 3.0)]}
    words = fill_word_times(seg)
    assert words[1]["start"] == 1.0 and words[1]["end"] == 1.5
    assert words[2]["start"] == 1.5 and words[2]["end"] == 2.0


def test_all_missing_times_spread_over_segment():
    seg = {"start": 10, "end": 12, "text": "", "words": [{"text": "一"}, {"text": "二"}]}
    words = fill_word_times(seg)
    assert (words[0]["start"], words[0]["end"], words[1]["start"], words[1]["end"]) == (10, 11, 11, 12)


def test_no_words_uses_segment_text():
    out = assign_speakers([{"start": 1, "end": 2, "text": "整段", "words": []}], [(0, 5, "A")])
    assert out == [{"id": 0, "start": 1.0, "end": 2.0, "speaker": "A", "text": "整段"}]


def test_no_diarization_keeps_segments_without_speaker():
    segs = [{"start": 0, "end": 1, "text": "", "words": [w("嗨", 0, 1)]}]
    out = assign_speakers(segs, None)
    assert out[0]["speaker"] is None and out[0]["text"] == "嗨"


def test_word_outside_turns_uses_nearest_or_previous():
    assert speaker_for(5.0, 5.2, [(0, 4.5, "A"), (6.0, 8, "B")]) == "A"
    assert speaker_for(5.6, 5.8, [(0, 4.5, "A"), (6.0, 8, "B")]) == "B"
    assert speaker_for(20, 21, [(0, 4.5, "A")]) is None
    # 離所有區段都太遠的字沿用前一個字的講者
    words = [w("a", 0, 1), w("b", 20, 21)]
    out = assign_speakers([{"start": 0, "end": 21, "text": "", "words": words}], [(0, 1, "A")])
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "ab")]


def test_spaces_inherit_speaker_and_english_text_is_kept():
    words = [w("Hello", 0, .5), w(" ", None, None), w(" world", .6, 1.0), w("你", 3, 3.3)]
    out = assign_speakers([{"start": 0, "end": 4, "text": "", "words": words}], [(0, 1.2, "A"), (2.5, 4, "B")])
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "Hello  world"), ("B", "你")]


def test_restore_spaces_for_chinese_alignment():
    from app.pipeline.align import restore_spaces

    text = "我用 open source 軟體"
    chars = [c for c in text if c != " "]
    words = restore_spaces(text, [{"text": c, "start": 0, "end": 0} for c in chars], spaced=False)
    assert "".join(w["text"] for w in words) == "我用 open source 軟體"
    # 有空格的語言：字之間本來就有空白
    words = restore_spaces("hello big world", [{"text": t} for t in ["hello", "big", "world"]], spaced=True)
    assert "".join(w["text"] for w in words) == "hello big world"


def test_segment_language_grouping():
    from app.pipeline.align import group_by_language, segment_language

    assert segment_language("我們用 Docker 部署", "en") == "zh"
    assert segment_language("we use Docker", "zh") == "en"
    assert segment_language("123。", "zh") == "zh"
    assert segment_language("日本語です", "ja") == "ja"
    groups = group_by_language(
        [{"text": "你好"}, {"text": "hello there"}, {"text": "再見 bye"}], "zh")
    assert {k: [s["text"] for s in v] for k, v in groups.items()} == {
        "zh": ["你好", "再見 bye"], "en": ["hello there"]}


def _seg(words):
    return {"start": words[0]["start"], "end": words[-1]["end"], "text": "", "words": words}


def test_short_segment_between_same_speaker_segments_is_absorbed():
    # 「able to see this.」(1.3s) 自成一個 ASR 段落，前後兩段都是 A，但 diarization 把它判給 B
    segs = [
        _seg([w("Can", 0, .8), w(" you", .8, 1.6), w(" hear", 1.6, 2.4), w(" me", 2.4, 3.2)]),
        _seg([w("able", 3.3, 3.6), w(" to", 3.6, 3.9), w(" see", 3.9, 4.2), w(" this.", 4.2, 4.6)]),
        _seg([w("Great", 4.7, 5.5), w(" thanks", 5.5, 6.3), w(" a", 6.3, 7.0)]),
    ]
    turns = [(0, 3.25, "A"), (3.25, 4.65, "B"), (4.65, 9, "A")]
    out = assign_speakers(segs, turns)
    # ASR 段落邊界保留，但講者都是 A
    assert [(s["speaker"], s["text"]) for s in out] == [
        ("A", "Can you hear me"), ("A", "able to see this."), ("A", "Great thanks a")]


def test_chinese_per_character_short_run_is_absorbed():
    # 「然後要上 50」每字一個 word、共 0.7 秒：舊的字數門檻吸收不了
    chars = "然後要上五十"
    words = [w(c, 3.7 + 0.12 * i, 3.7 + 0.12 * (i + 1)) for i, c in enumerate(chars)]
    before = [w(c, 0.6 * i, 0.6 * (i + 1)) for i, c in enumerate("我們來看一下")]  # 3.6 秒
    after = [w(c, 4.5 + 0.2 * i, 4.5 + 0.2 * (i + 1)) for i, c in enumerate("這個功能")]
    turns = [(0, 3.65, "A"), (3.65, 4.45, "B"), (4.45, 6, "A")]
    out = assign_speakers([_seg(before), _seg(words), _seg(after)], turns)
    assert [s["speaker"] for s in out] == ["A", "A", "A"]
    assert [s["text"] for s in out] == ["我們來看一下", "然後要上五十", "這個功能"]


def test_real_speaker_change_is_not_changed():
    segs = [_seg([w("你", 0, .5), w("好", .5, 1.0)]), _seg([w("再", 1.0, 1.5), w("見", 1.5, 2.0)])]
    out = assign_speakers(segs, [(0, 1.0, "A"), (1.0, 2.0, "B")])
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "你好"), ("B", "再見")]


def test_short_interjection_with_pauses_beyond_threshold_is_kept():
    # B 的插話 2.0 秒（> 1.5），前後各有 0.5 秒停頓（不算連續講話）→ 保留
    words = [w("今", 0, .5), w("天", .5, 1.0),
             w("對", 1.5, 2.5), w("啊", 2.5, 3.5),
             w("開", 4.0, 4.5), w("會", 4.5, 5.0)]
    turns = [(0, 1.2, "A"), (1.2, 3.8, "B"), (3.8, 6, "A")]
    out = assign_speakers([_seg(words)], turns)
    assert [s["speaker"] for s in out] == ["A", "B", "A"]


def test_medium_run_with_continuous_speech_is_absorbed():
    # 2.0 秒（1.5 < 時長 <= 2.5）但前後無停頓 → 視為連續講話，吸收
    words = [w("a", 0, 2.0), w("b", 2.0, 4.0), w("c", 4.0, 5.0), w("d", 5.0, 6.0), w("e", 6.0, 8.0)]
    labels = ["A", "A", "B", "B", "A"]
    assert smooth_labels(words, labels) == ["A"] * 5
    # 同樣長度但前面有 0.5 秒停頓 → 保留
    words2 = [w("a", 0, 2.0), w("b", 2.0, 4.0), w("c", 4.5, 5.5), w("d", 5.5, 6.5), w("e", 6.5, 8.5)]
    assert smooth_labels(words2, labels) == labels


def test_run_at_sequence_edges_is_not_smoothed():
    words = [w("a", 0, .3), w("b", .3, .6), w("c", .6, .9)]
    assert smooth_labels(words, ["B", "A", "A"]) == ["B", "A", "A"]
    assert smooth_labels(words, ["A", "A", "B"]) == ["A", "A", "B"]
    assert smooth_labels(words, [None, "B", "A"]) == [None, "B", "A"]


def test_boundary_moves_to_nearby_pause():
    # 新講者 B 的標籤比實際早兩個字：「飽」「嗎」其實還是 A，0.5 秒停頓在「嗎」之後
    words = [w("吃", 0, .2), w("到", .2, .4), w("飽", .4, .55), w("嗎", .55, .7),
             w("這", 1.2, 1.4), w("算", 1.4, 1.6), w("是", 1.6, 1.8)]
    turns = [(0, .45, "A"), (.45, 4, "B")]
    out = assign_speakers([_seg(words)], turns)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "吃到飽嗎"), ("B", "這算是")]


def test_boundary_moves_left_to_pause_too():
    # 標籤比實際晚：「這算」其實是 B，停頓在「嗎」「這」之間
    words = [w("吃", 0, .3), w("到", .3, .6), w("嗎", .6, .9),
             w("這", 1.4, 1.7), w("算", 1.7, 2.0), w("是", 2.0, 2.3)]
    turns = [(0, 1.8, "A"), (1.8, 4, "B")]
    out = assign_speakers([_seg(words)], turns)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "吃到嗎"), ("B", "這算是")]


def test_boundary_not_moved_beyond_search_window_or_small_pause():
    # 停頓在 1.5 秒外 → 不移
    words = [w("a", 0, .3), w("b", .3, .6), w("c", .6, .9), w("d", .9, 1.2), w("e", 1.2, 1.5),
             w("f", 1.5, 1.8), w("g", 1.8, 2.1), w("h", 2.1, 2.4), w("i", 3.0, 3.3)]
    labels_in = ["A"] * 4 + ["B"] * 5
    assert align_boundaries(words, labels_in) == labels_in
    # 停頓只有 0.1 秒（< 0.15）→ 不移
    words2 = [w("a", 0, .3), w("b", .3, .6), w("c", .7, 1.0), w("d", 1.0, 1.3)]
    assert align_boundaries(words2, ["A", "B", "B", "B"]) == ["A", "B", "B", "B"]


def test_boundary_ignores_interpolated_times():
    # 中間兩個字沒有時間戳（內插），「0.9 秒停頓」只存在於內插時間之間 → 不可用
    seg = {"start": 0, "end": 3, "text": "", "words": [
        w("a", 0, .3), {"text": "b"}, {"text": "c"}, w("d", 2.0, 2.3), w("e", 2.3, 2.6)]}
    words = fill_word_times(seg)
    assert words[1]["_interp"] and words[2]["_interp"]
    assert not words[0].get("_interp") and not words[3].get("_interp")
    labels = ["A", "A", "B", "B", "B"]
    assert align_boundaries(words, labels) == labels


def test_boundary_move_never_removes_run_or_crosses_other_change():
    # A B A 三段；B 只有一個字，最大停頓在 B 之後 → 邊界最多移到不讓 B 消失
    words = [w("a", 0, .3), w("b", .3, .6), w("c", 1.5, 1.8), w("d", 1.8, 2.1)]
    out = align_boundaries(words, ["A", "B", "B", "B"])
    assert out[0] == "A" and "B" in out
    labels = ["A", "B", "C", "C"]
    out = align_boundaries(words, labels)
    assert set(out) == {"A", "B", "C"} and out[3] == "C"
    # 順序不變（沒有跨越其他變化點）
    seen = [out[0]] + [out[i] for i in range(1, len(out)) if out[i] != out[i - 1]]
    assert seen == ["A", "B", "C"]


# ---- 保守化：不連鎖、鄰居須夠長、短 run 也看停頓 ----


def test_rapid_alternating_questions_and_answers_are_kept():
    # A/B 交替 8 句，每句 0.4~0.8 秒、句間停 0.8 秒：兩側都是短句，不可被吃成全部 A
    words, labels, t = [], [], 0.0
    for i in range(8):
        dur = 0.4 + 0.1 * (i % 5)
        words.append(w("句", round(t, 2), round(t + dur, 2)))
        labels.append("AB"[i % 2])
        t += dur + 0.8
    assert smooth_labels(words, labels) == labels
    segs = [_seg([x]) for x in words]
    turns = [(x["start"] - .05, x["end"] + .05, lab) for x, lab in zip(words, labels)]
    out = assign_speakers(segs, turns)
    assert [o["speaker"] for o in out] == labels


def test_smoothing_is_not_chained_and_uses_original_runs():
    # A(長) B A B A(長)：中間 A 兩側都是 0.3 秒的 B，不會因為 B 被吸收而連鎖；結果只依原始 runs 決定
    words = [w("a", 0, 4.0), w("b", 4.0, 4.3), w("c", 4.3, 4.6), w("d", 4.6, 4.9), w("e", 4.9, 9.0)]
    assert smooth_labels(words, ["A", "B", "A", "B", "A"]) == ["A"] * 5
    # 兩側鄰居都短：保留
    short = [w("a", 0, 1.0), w("b", 1.0, 1.8), w("c", 1.8, 2.6), w("d", 2.6, 3.4), w("e", 3.4, 4.5)]
    assert smooth_labels(short, ["A", "B", "A", "B", "A"]) == ["A", "B", "A", "B", "A"]


def test_neighbor_must_be_long_enough_relative_to_the_short_run():
    # 鄰居 3.0 秒夠長 → 吸收；鄰居 2.9 秒 → 保留
    ok = [w("a", 0, 3.0), w("b", 3.0, 4.0), w("c", 4.0, 5.0)]
    assert smooth_labels(ok, ["A", "B", "A"]) == ["A", "A", "A"]
    no = [w("a", 0, 2.9), w("b", 2.9, 3.9), w("c", 3.9, 4.9)]
    assert smooth_labels(no, ["A", "B", "A"]) == ["A", "B", "A"]


def test_short_run_separated_by_long_silence_is_kept():
    # 「你明天會來參加會議嗎」(A) 停 2 秒「會」(B) 停 2 秒「好那我們就這樣決定」(A) → B 保留
    q = [w(c, 0.4 * i, 0.4 * (i + 1)) for i, c in enumerate("你明天會來參加會議嗎")]  # 0~3.6
    a = [w("會", 5.6, 6.0)]
    r = [w(c, 8.0 + 0.4 * i, 8.0 + 0.4 * (i + 1)) for i, c in enumerate("好那我們就這樣決定")]
    out = assign_speakers([_seg(q), _seg(a), _seg(r)], [(0, 5, "A"), (5, 7, "B"), (7, 13, "A")])
    assert [(o["speaker"], o["text"]) for o in out] == [
        ("A", "你明天會來參加會議嗎"), ("B", "會"), ("A", "好那我們就這樣決定")]


def test_boundary_with_real_pause_is_not_moved():
    # C2：ASR 段落邊界停 0.3 秒就是換人處，前段「可以」「吧」間停 0.4 秒 → 「吧」不可被移給 B
    seg1 = _seg([w("可", 0, .3), w("以", .3, .6), w("吧", 1.0, 1.3)])
    seg2 = _seg([w("好", 1.6, 1.9), w("的", 1.9, 2.2)])
    out = assign_speakers([seg1, seg2], [(0, 1.45, "A"), (1.45, 4, "B")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "可以吧"), ("B", "好的")]
    # D：邊界停 0.25 秒、下一段「嗯」後停 0.35 秒 → 不可切出單獨「嗯」
    seg1 = _seg([w("對", 0, .3), w("啊", .3, .6)])
    seg2 = _seg([w("嗯", .85, 1.15), w("我", 1.5, 1.8), w("想", 1.8, 2.1)])
    out = assign_speakers([seg1, seg2], [(0, .7, "A"), (.7, 4, "B")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "對啊"), ("B", "嗯我想")]


def test_boundary_search_stops_when_time_leaves_window():
    # 時間交錯（倒序）的字不會讓搜尋跳到很遠的位置：窗外的大停頓不採用
    words = [w("a", 0, .3), w("b", .3, .6), w("c", .6, .9), w("d", .9, 1.2), w("e", 1.2, 1.5),
             w("f", 1.5, 1.8), w("g", 10.0, 10.3), w("h", 1.8, 2.1), w("i", 2.1, 2.4)]
    labels = ["A"] * 3 + ["B"] * 6
    assert align_boundaries(words, labels) == labels


# ---- speaker_for 索引版與原版一致、規模 ----


def test_turn_index_matches_speaker_for_on_random_turns():
    import random

    from app.pipeline.assign import _TurnIndex

    r = random.Random(11)
    for case in range(200):
        n = r.randint(0, 12)
        overlapping = case % 2 == 0
        t, turns = 0.0, []
        for _ in range(n):
            if overlapping:
                a = r.uniform(0, 30)
                turns.append((a, a + r.uniform(0, 6), r.choice("ABC")))
            else:
                a = t + r.choice([0.0, 0.5, 3.0])
                turns.append((a, a + r.uniform(0.0, 4), r.choice("ABC")))
                t = turns[-1][1]
        turns.sort(key=lambda x: x[0])
        idx = _TurnIndex(turns)
        for _ in range(60):
            a = r.uniform(-3, 40)
            b = a + r.choice([0.0, 0.0, r.uniform(0, 2)])
            assert idx.lookup(a, b) == speaker_for(a, b, turns), (turns, a, b)


def test_speaker_assignment_scales_with_many_turns():
    import time

    words = [w("字", round(0.3 * i, 2), round(0.3 * i + 0.25, 2)) for i in range(30000)]
    turns = [(i * 3.0, i * 3.0 + 3.0, "AB"[i % 2]) for i in range(3000)]
    segs = [_seg(words[i:i + 25]) for i in range(0, 30000, 25)]
    t0 = time.perf_counter()
    out = assign_speakers(segs, turns)
    assert time.perf_counter() - t0 < 3.0
    assert sum(len(o["text"]) for o in out) == 30000


# ---- 輸出時間、開頭沒有講者 ----


def test_output_times_ignore_whitespace_words():
    # 空白字沒有時間戳（內插）：不可拉長 end
    words = [w("Hi", 0, .3), {"text": " "}, w("yes", 5.0, 5.3), {"text": " "}]
    out = assign_speakers([{"start": 0, "end": 9, "text": "", "words": words}], [(0, 9, "A")])
    assert (out[0]["start"], out[0]["end"]) == (0.0, 5.3)


def test_leading_words_without_any_turn_get_following_speaker():
    segs = [_seg([w("a", 0, .5)]), _seg([w("c", 10, 11)])]
    out = assign_speakers(segs, [(10, 12, "A")])
    assert [o["speaker"] for o in out] == ["A", "A"]


# ---- 連續語音中的極短 run（<= 0.6 秒、停頓 < 0.2 秒）免除鄰居條件 ----


def test_tiny_continuous_flip_is_absorbed_even_with_short_neighbors():
    # 原始情境：A 0.4、「嗯」0.1（被判 B）、A 0.4，全部連續
    words = [w("今", 0, .2), w("天", .2, .4), w("嗯", .4, .5), w("開", .5, .7), w("會", .7, .9)]
    labels = ["A", "A", "B", "A", "A"]
    assert smooth_labels(words, labels) == ["A"] * 5
    turns = [(0, .4, "A"), (.4, .5, "B"), (.5, 1, "A")]
    out = assign_speakers([{"start": 0, "end": 1, "text": "", "words": words}], turns)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "今天嗯開會")]


def test_tiny_run_with_pause_or_just_over_limit_needs_long_neighbor():
    # 停頓 0.3 秒（>= 0.2）→ 不算連續，鄰居短就保留
    words = [w("今", 0, .4), w("嗯", .7, 1.0), w("開", 1.3, 1.7)]
    assert smooth_labels(words, ["A", "B", "A"]) == ["A", "B", "A"]
    # 連續但 0.7 秒（> 0.6）→ 不免除，鄰居短就保留
    words = [w("今", 0, .4), w("嗯", .4, 1.1), w("開", 1.1, 1.5)]
    assert smooth_labels(words, ["A", "B", "A"]) == ["A", "B", "A"]


def test_rapid_qa_with_pauses_still_kept_when_runs_are_tiny():
    # 每句 0.4~0.6 秒、句間停 0.8 秒：雖然是極短 run，但不是連續語音 → 保留
    words, labels, t = [], [], 0.0
    for i in range(8):
        words.append(w("句", round(t, 2), round(t + 0.5, 2)))
        labels.append("AB"[i % 2])
        t += 1.3
    assert smooth_labels(words, labels) == labels


# ---- 相鄰兩個 run 都是候選：時長較短者優先 ----


def test_adjacent_candidates_prefer_the_shorter_run_H():
    # H：A 講 10s、停 0.4s、X 講 1.0s（連續）、A blip 0.2s、X 講 10s → blip 併入 X，X 的開頭不被舊講者吃掉
    words = [w("甲", 0, 10.0), w("乙", 10.4, 11.4), w("丙", 11.4, 11.6), w("丁", 11.6, 21.6)]
    assert smooth_labels(words, ["A", "X", "A", "X"]) == ["A", "X", "X", "X"]
    segs = [_seg([words[0]]), _seg(words[1:])]
    out = assign_speakers(segs, [(0, 10.2, "A"), (10.2, 11.4, "X"), (11.4, 11.6, "A"), (11.6, 25, "X")])
    assert [(o["speaker"], o["text"]) for o in out] == [("A", "甲"), ("X", "乙丙丁")]


def test_adjacent_candidates_prefer_the_shorter_run_mirror_H():
    # H'：鏡像。X 講 10s、A blip 0.2s、X 講 1.0s、停 0.4s、A 講 10s → blip 併入 X
    words = [w("甲", 0, 10.0), w("乙", 10.0, 10.2), w("丙", 10.2, 11.2), w("丁", 11.6, 21.6)]
    assert smooth_labels(words, ["X", "A", "X", "A"]) == ["X", "X", "X", "A"]


def test_adjacent_candidates_are_never_swapped():
    # 兩個相鄰候選時同時只吸收一個，不會互換成 X X Y Y
    words = [w("a", 0, 4.0), w("b", 4.0, 4.4), w("c", 4.4, 4.9), w("d", 4.9, 9.0)]
    # b（0.4s）比 c（0.5s）短，先被挑選；c 是 b 的相鄰 run，不可再被挑
    assert smooth_labels(words, ["X", "Y", "X", "Y"]) == ["X", "X", "X", "Y"]


def test_turn_index_nearest_exactly_two_seconds_matches_speaker_for():
    from app.pipeline.assign import _TurnIndex

    turns = [(0.0, 0.1, "A"), (5.3, 6.0, "B")]
    for start, end in [(2.1, 2.2), (0.1 + 2.0, 2.3), (3.3, 3.3 + 1e-9), (3.3, 3.4)]:
        assert _TurnIndex(turns).lookup(start, end) == speaker_for(start, end, turns)


# ---- 一側緊貼、另一側有停頓的短 run（真實錄音的剩餘誤判）----


def _attached_case(dur, prev_gap, next_gap, prev_len=60.0, next_len=60.0):
    """A(長) -prev_gap- B(dur) -next_gap- A(長)，回傳 (words, labels)。"""
    t = 0.0
    words = [w("前", t, t + prev_len)]
    t += prev_len + prev_gap
    words.append(w("短", t, t + dur))
    t += dur + next_gap
    words.append(w("後", t, t + next_len))
    return words, ["A", "B", "A"]


@pytest.mark.parametrize("dur,prev_gap,next_gap", [
    (0.24, 0.00, 2.06),  # 產品
    (0.16, 0.00, 1.74),  # 答案
    (1.59, 0.00, 0.54),  # 相信大家都有不同的上下文
    (2.05, 0.98, 0.00),  # 然後另外一個客
    (0.08, 1.03, 0.00),  # 接下
    (0.32, 2.02, 0.00),  # 那還是回
])
def test_run_attached_on_one_side_is_absorbed(dur, prev_gap, next_gap):
    words, labels = _attached_case(dur, prev_gap, next_gap)
    assert smooth_labels(words, labels) == ["A", "A", "A"]


def test_attached_rule_respects_limits():
    # 兩側都有長停頓 → 保留
    words, labels = _attached_case(0.3, 2.0, 2.0)
    assert smooth_labels(words, labels) == labels
    # 超過 2.5 秒 → 保留
    words, labels = _attached_case(2.6, 0.0, 1.5)
    assert smooth_labels(words, labels) == labels
    # 緊貼那一側的鄰居太短（< 3 秒）→ 保留
    words, labels = _attached_case(1.0, 0.0, 1.5, prev_len=2.9, next_len=60.0)
    assert smooth_labels(words, labels) == labels
    # 緊貼那一側的鄰居夠長、另一側（短）鄰居不影響
    words, labels = _attached_case(1.0, 0.0, 1.5, prev_len=60.0, next_len=1.0)
    assert smooth_labels(words, labels) == ["A", "A", "A"]


def test_attached_rule_ignores_interpolated_times():
    # 緊貼那一側的字是內插時間 → 視為未知，不採用
    words, labels = _attached_case(1.0, 0.0, 1.5)
    words[1]["_interp"] = True
    assert smooth_labels(words, labels) == labels


def test_attached_rule_across_asr_segments_end_to_end():
    segs = [_seg([w("前", 0, 60.0)]), _seg([w("短", 60.0, 60.3)]), _seg([w("後", 62.4, 120.0)])]
    out = assign_speakers(segs, [(0, 60.0, "A"), (60.0, 60.3, "B"), (60.3, 130, "A")])
    assert [o["speaker"] for o in out] == ["A", "A", "A"]


def test_attached_rule_keeps_qa_and_long_silence_answers():
    # A/B 交替 8 句、句間停 0.8 秒
    words, labels, t = [], [], 0.0
    for i in range(8):
        words.append(w("句", round(t, 2), round(t + 2.0, 2)))
        labels.append("AB"[i % 2])
        t += 2.8
    assert smooth_labels(words, labels) == labels
    # 長靜默隔開的短回答（前後各停 2 秒）
    words, labels = _attached_case(0.5, 2.0, 2.0)
    assert smooth_labels(words, labels) == labels

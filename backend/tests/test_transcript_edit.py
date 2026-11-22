import pytest

from app import transcript as tr


def sample():
    return {
        "speakers": {
            "SPEAKER_00": {"name": None, "voiceprint_id": None, "similarity": None},
            "SPEAKER_01": {"name": "Amy", "voiceprint_id": 3, "similarity": 0.8},
        },
        "speaker_embeddings": {"SPEAKER_00": [1.0]},
        "segments": [
            {"id": 0, "start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "一"},
            {"id": 1, "start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "二"},
            {"id": 2, "start": 2, "end": 3, "speaker": "SPEAKER_00", "text": "三"},
        ],
    }


def test_make_edited_is_deep_copy_without_embeddings():
    orig = sample()
    ed = tr.make_edited(orig)
    ed["segments"][0]["text"] = "改"
    assert orig["segments"][0]["text"] == "一"
    assert "speaker_embeddings" not in ed


def test_rename_and_display_name():
    t = sample()
    tr.apply_edit(t, {"op": "rename_speaker", "speaker": "SPEAKER_00", "name": " Alex "})
    assert tr.display_name(t, "SPEAKER_00") == "Alex"
    tr.apply_edit(t, {"op": "rename_speaker", "speaker": "SPEAKER_00", "name": ""})
    # 名字清空：回到預設的「講者 N」（N＝代號數字＋1）
    assert tr.display_name(t, "SPEAKER_00") == "講者 1"


def test_set_text():
    t = sample()
    tr.apply_edit(t, {"op": "set_text", "segment_id": 1, "text": " 二二 "})
    assert t["segments"][1]["text"] == "二二"
    with pytest.raises(tr.EditError):
        tr.apply_edit(t, {"op": "set_text", "segment_id": 99, "text": "x"})


def test_set_speaker_existing_and_new():
    t = sample()
    tr.apply_edit(t, {"op": "set_speaker", "segment_id": 1, "speaker": "SPEAKER_00"})
    assert t["segments"][1]["speaker"] == "SPEAKER_00"
    # SPEAKER_01 沒有段落了 → 從講者清單移除
    assert "SPEAKER_01" not in t["speakers"]
    tr.apply_edit(t, {"op": "set_speaker", "segment_id": 2, "speaker": None, "new_name": "Bob"})
    new_label = t["segments"][2]["speaker"]
    # 手動新增的講者用 MANUAL_ 前綴，不會拿到被移除的 SPEAKER_01
    assert new_label == "MANUAL_00" and t["speakers"][new_label]["name"] == "Bob"
    with pytest.raises(tr.EditError):
        tr.apply_edit(t, {"op": "set_speaker", "segment_id": 0, "speaker": "NOPE"})


def test_merge_speakers():
    t = sample()
    tr.apply_edit(t, {"op": "merge_speakers", "source": "SPEAKER_00", "target": "SPEAKER_01"})
    assert {s["speaker"] for s in t["segments"]} == {"SPEAKER_01"}
    assert list(t["speakers"]) == ["SPEAKER_01"]
    with pytest.raises(tr.EditError):
        tr.apply_edit(t, {"op": "merge_speakers", "source": "SPEAKER_01", "target": "SPEAKER_01"})


def test_unknown_op():
    with pytest.raises(tr.EditError):
        tr.apply_edit(sample(), {"op": "drop_table"})


# ---- 講者代號不重複使用（避免聲紋存成錯的人） ----


def test_new_speaker_after_merge_does_not_reuse_merged_label():
    """合併掉 SPEAKER_00 之後再新增講者，不能拿到 SPEAKER_00（它在原始結果裡有別人的聲紋）。"""
    original = sample()
    t = tr.make_edited(original)
    reserved = tr.reserved_labels(original)
    tr.apply_edit(t, {"op": "merge_speakers", "source": "SPEAKER_00", "target": "SPEAKER_01"}, reserved)
    tr.apply_edit(t, {"op": "set_speaker", "segment_id": 0, "speaker": None, "new_name": "Bob"}, reserved)
    label = t["segments"][0]["speaker"]
    assert label.startswith("MANUAL_") and label not in reserved
    # 原始結果有 SPEAKER_00 的 embedding，但新講者不能被當成可存聲紋的人
    assert tr.voiceprint_labels(original, t) == []


def test_manual_label_avoids_reserved_and_existing():
    t = {"speakers": {"MANUAL_00": {"name": "A"}}, "segments": [{"id": 0, "speaker": "MANUAL_00", "text": "x"}]}
    tr.set_segment_speaker(t, 0, None, "B", reserved={"MANUAL_01"})
    assert t["segments"][0]["speaker"] == "MANUAL_02"


def test_reserved_labels_include_embeddings_without_speaker_entry():
    original = {"speakers": {"SPEAKER_00": {}}, "speaker_embeddings": {"SPEAKER_00": [1.0], "SPEAKER_03": [0.5]}}
    assert tr.reserved_labels(original) == {"SPEAKER_00", "SPEAKER_03"}
    assert tr.reserved_labels(None) == set()


def test_voiceprint_labels_only_same_person_with_embedding():
    original = sample()
    current = tr.make_edited(original)
    # SPEAKER_01 沒有 embedding；SPEAKER_00 有
    assert tr.voiceprint_labels(original, current) == ["SPEAKER_00"]
    # 舊版資料：把手動新增的講者取名成 SPEAKER_00，而且原本 SPEAKER_00 的段落都已經不是它
    legacy = tr.make_edited(original)
    for seg in legacy["segments"]:
        seg["speaker"] = "SPEAKER_01" if seg["speaker"] == "SPEAKER_00" else seg["speaker"]
    legacy["segments"][1]["speaker"] = "SPEAKER_00"  # 原本是 SPEAKER_01 的段落被掛到「新的」SPEAKER_00
    assert tr.voiceprint_labels(original, legacy) == []

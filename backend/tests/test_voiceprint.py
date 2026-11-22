from app import voiceprint


def test_cosine():
    assert voiceprint.cosine([1, 0], [1, 0]) == 1.0
    assert abs(voiceprint.cosine([1, 0], [0, 1])) < 1e-9
    assert voiceprint.cosine([1, 0], [1, 0, 0]) == -1.0
    assert voiceprint.cosine([0, 0], [1, 0]) == -1.0


def test_match_one_to_one_by_best_similarity():
    emb = {"A": [1.0, 0.0], "B": [0.9, 0.1]}
    vps = [{"id": 1, "name": "甲", "embedding": [1.0, 0.0]}]
    out = voiceprint.match(emb, vps, threshold=0.5)
    # 同一個聲紋只配給最像的講者
    assert list(out) == ["A"] and out["A"]["name"] == "甲"


def test_match_threshold():
    out = voiceprint.match({"A": [1.0, 0.0]}, [{"id": 1, "name": "甲", "embedding": [0.6, 0.8]}], 0.7)
    assert out == {}


def test_store_roundtrip(data_dir):
    vp = voiceprint.create("甲", [0.1, 0.2], "m1", "upload", 12.0)
    voiceprint.create("乙", [0.3, 0.4], "m2", "upload", 12.0)
    assert [v["name"] for v in voiceprint.list_all("m1")] == ["甲"]
    assert voiceprint.rename(vp["id"], "丙")
    assert voiceprint.list_all("m1")[0]["name"] == "丙"
    assert voiceprint.delete(vp["id"])
    assert not voiceprint.delete(vp["id"])

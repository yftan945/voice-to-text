"""逐字指派講者並在換講者處切句（純 Python，不依賴 GPU，可單元測試）。

輸入：
- asr_segments：[{start, end, text, words: [{text, start, end}]}]，words 可為空或缺時間
- turns：[(start, end, speaker)] 講者分段（建議用 exclusive diarization，沒有重疊）

輸出：[{id, start, end, speaker, text}]
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Sequence

Turn = tuple[float, float, str]

# 被同一講者夾住（前後 run 是同一人）的短片段視為誤判，改回前後的講者。以時間為主、不看字數
#（中文逐字對齊後一個字就是一個 word）。只依「原始 runs」一次掃描決定、同時套用（不連鎖），
# 吸收條件（全部成立）：
# - 時長 <= SMOOTH_MAX_SECONDS 且與前後 run 之間的停頓都 < SMOOTH_SHORT_MAX_PAUSE；
#   或時長 <= SMOOTH_LONG_MAX_SECONDS 且停頓都 < SMOOTH_MAX_PAUSE（連續講話）
# - 至少一側鄰居 run 時長 >= max(SMOOTH_NEIGHBOR_MIN_SECONDS, SMOOTH_NEIGHBOR_RATIO × 本身時長)
#   （快速一問一答時兩側都是短句，不會被吃掉）
#   例外：時長 <= SMOOTH_TINY_MAX_SECONDS 且前後停頓都 < SMOOTH_MAX_PAUSE（連續語音中的「嗯」之類）
#   免除鄰居條件
# - 另一條「一側緊貼」：時長 <= SMOOTH_ATTACHED_MAX_SECONDS，且至少一側與鄰居的停頓（只用真實時間戳，
#   內插時間視為未知）< SMOOTH_MAX_PAUSE，且那一側鄰居 run 時長 >= max(SMOOTH_NEIGHBOR_MIN_SECONDS,
#   SMOOTH_NEIGHBOR_RATIO × 本身時長)；另一側停頓不限（句子被切到別人身上，另一側是自然停頓）
SMOOTH_MAX_SECONDS = 1.5
SMOOTH_SHORT_MAX_PAUSE = 1.0
SMOOTH_LONG_MAX_SECONDS = 2.5
SMOOTH_MAX_PAUSE = 0.2
SMOOTH_NEIGHBOR_MIN_SECONDS = 3.0
SMOOTH_NEIGHBOR_RATIO = 2.0
SMOOTH_TINY_MAX_SECONDS = 0.6
SMOOTH_ATTACHED_MAX_SECONDS = 2.5
# 換講者的邊界移到附近最大的停頓：搜尋範圍（秒）與最小停頓（秒）。
# 原邊界已有 >= BOUNDARY_MIN_PAUSE 的真實停頓就不動
BOUNDARY_SEARCH_SECONDS = 0.6
BOUNDARY_MIN_PAUSE = 0.15
# 字完全沒落在任何講者區段時，往外找最近區段的最大距離（秒）
NEAREST_TURN_MAX_GAP = 2.0


def fill_word_times(segment: dict[str, Any]) -> list[dict[str, Any]]:
    """補齊沒有時間戳的字：用前後有時間的字內插，整段都沒有就平均分配段落時間。
    補出來的字會加上 `_interp: True`。"""
    words = [dict(w) for w in segment.get("words") or []]
    seg_start = float(segment.get("start") or 0.0)
    seg_end = float(segment.get("end") or seg_start)
    if not words:
        text = segment.get("text") or ""
        return [{"text": text, "start": seg_start, "end": seg_end}] if text.strip() else []

    known = [i for i, w in enumerate(words) if w.get("start") is not None and w.get("end") is not None]
    if not known:
        n = len(words)
        step = (seg_end - seg_start) / n if n else 0.0
        for i, w in enumerate(words):
            w["start"] = seg_start + step * i
            w["end"] = seg_start + step * (i + 1)
            w["_interp"] = True
        return words

    for i, w in enumerate(words):
        if w.get("start") is not None and w.get("end") is not None:
            continue
        prev_i = max((k for k in known if k < i), default=None)
        next_i = min((k for k in known if k > i), default=None)
        left = float(words[prev_i]["end"]) if prev_i is not None else seg_start
        right = float(words[next_i]["start"]) if next_i is not None else seg_end
        lo = prev_i if prev_i is not None else -1
        hi = next_i if next_i is not None else len(words)
        span = hi - lo
        frac0 = (i - lo - 1) / (span - 1) if span > 1 else 0.0
        frac1 = (i - lo) / (span - 1) if span > 1 else 1.0
        right = max(right, left)
        w["start"] = left + (right - left) * frac0
        w["end"] = left + (right - left) * min(frac1, 1.0)
        w["_interp"] = True  # 內插出來的時間不可靠，邊界對齊停頓時不採用
    return words


def speaker_for(start: float, end: float, turns: Sequence[Turn]) -> str | None:
    """找重疊最多的講者；完全沒有重疊時找距離最近（且不超過門檻）的講者。"""
    best: str | None = None
    best_overlap = 0.0
    for t0, t1, spk in turns:
        ov = min(end, t1) - max(start, t0)
        if ov > best_overlap:
            best_overlap = ov
            best = spk
    if best is not None:
        return best
    # 零長度的字：看中點落在哪個區段
    mid = (start + end) / 2
    for t0, t1, spk in turns:
        if t0 <= mid <= t1:
            return spk
    nearest: str | None = None
    nearest_gap = NEAREST_TURN_MAX_GAP
    for t0, t1, spk in turns:
        gap = t0 - end if t0 > end else (start - t1 if start > t1 else 0.0)
        if gap <= nearest_gap:
            nearest_gap = gap
            nearest = spk
    return nearest


class _TurnIndex:
    """把（依開始時間排序的）講者區段建索引，查詢時只掃描時間窗內的區段，結果與
    `speaker_for(start, end, turns)` 一致（重疊最多者優先、零長度看中點、否則最近者；
    最近者的搜尋窗放寬 1e-6 以免浮點誤差漏掉剛好 2 秒的區段）。
    輸入時間倒序（end < start）時不保證與 speaker_for 等價。"""

    def __init__(self, turns: Sequence[Turn]):
        self.turns = list(turns)
        self.starts = [float(t[0]) for t in self.turns]
        self.prefmax: list[float] = []  # prefmax[i] = max(end[0..i])，單調不減
        cur = float("-inf")
        for t in self.turns:
            cur = max(cur, float(t[1]))
            self.prefmax.append(cur)

    def lookup(self, start: float, end: float) -> str | None:
        turns = self.turns
        best: str | None = None
        best_overlap = 0.0
        lo = bisect_right(self.prefmax, start)  # 之前的區段結束時間都 <= start，不可能重疊
        hi = bisect_left(self.starts, end)  # 之後的區段開始時間都 >= end
        for i in range(lo, hi):
            t0, t1, spk = turns[i]
            ov = min(end, t1) - max(start, t0)
            if ov > best_overlap:
                best_overlap = ov
                best = spk
        if best is not None:
            return best
        mid = (start + end) / 2
        i = bisect_left(self.prefmax, mid)  # 第一個結束時間 >= mid 的區段
        if i < len(turns) and turns[i][0] <= mid:
            return turns[i][2]
        nearest: str | None = None
        nearest_gap = NEAREST_TURN_MAX_GAP
        lo = bisect_left(self.prefmax, start - NEAREST_TURN_MAX_GAP - 1e-6)
        hi = bisect_right(self.starts, end + NEAREST_TURN_MAX_GAP + 1e-6)
        for i in range(lo, hi):
            t0, t1, spk = turns[i]
            gap = t0 - end if t0 > end else (start - t1 if start > t1 else 0.0)
            if gap <= nearest_gap:
                nearest_gap = gap
                nearest = spk
        return nearest


def _runs(labels: list[str | None]) -> list[tuple[int, int, str | None]]:
    runs: list[tuple[int, int, str | None]] = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            runs.append((start, i, labels[start]))
            start = i
    return runs


def _real_gap(words: list[dict[str, Any]], i: int) -> float | None:
    """words[i-1] 與 words[i] 之間的停頓（略過空白字）；任一側是內插時間就回傳 None（未知）。"""
    j, k = i - 1, i
    while j >= 0 and _blank(words[j]):
        j -= 1
    while k < len(words) and _blank(words[k]):
        k += 1
    if j < 0 or k >= len(words) or words[j].get("_interp") or words[k].get("_interp"):
        return None
    return max(0.0, float(words[k]["start"]) - float(words[j]["end"]))


def smooth_labels(words: list[dict[str, Any]], labels: list[str | None]) -> list[str | None]:
    """被同一講者夾住的短 run 改回前後的講者（words 可跨 ASR 段落，整份逐字稿一起處理）。
    只依原始 runs 判斷、不連鎖：先找出所有符合條件的候選，再依時長由小到大貪婪挑選，
    已挑選者的相鄰 run 不可再被挑（否則相鄰兩個候選會互換講者）。O(n + r log r)。"""
    labels = list(labels)
    runs = _runs(labels)
    if len(runs) < 3:
        return labels
    spans = [max(0.0, float(words[e - 1]["end"]) - float(words[s]["start"])) for s, e, _ in runs]
    candidates: list[int] = []
    for k in range(1, len(runs) - 1):
        s, e, lab = runs[k]
        prev_lab = runs[k - 1][2]
        if prev_lab is None or prev_lab != runs[k + 1][2] or lab == prev_lab:
            continue
        dur = spans[k]
        gap_before = max(0.0, float(words[s]["start"]) - float(words[s - 1]["end"]))
        gap_after = max(0.0, float(words[e]["start"]) - float(words[e - 1]["end"]))
        gap = max(gap_before, gap_after)
        tiny = dur <= SMOOTH_TINY_MAX_SECONDS and gap < SMOOTH_MAX_PAUSE
        need = max(SMOOTH_NEIGHBOR_MIN_SECONDS, SMOOTH_NEIGHBOR_RATIO * dur)
        if tiny or max(spans[k - 1], spans[k + 1]) >= need:
            if (dur <= SMOOTH_MAX_SECONDS and gap < SMOOTH_SHORT_MAX_PAUSE) or (
                dur <= SMOOTH_LONG_MAX_SECONDS and gap < SMOOTH_MAX_PAUSE
            ):
                candidates.append(k)
                continue
        if dur <= SMOOTH_ATTACHED_MAX_SECONDS:
            gb, ga = _real_gap(words, s), _real_gap(words, e)
            if (gb is not None and gb < SMOOTH_MAX_PAUSE and spans[k - 1] >= need) or (
                ga is not None and ga < SMOOTH_MAX_PAUSE and spans[k + 1] >= need
            ):
                candidates.append(k)
    chosen: set[int] = set()
    for k in sorted(candidates, key=lambda k: (spans[k], k)):
        if k - 1 in chosen or k + 1 in chosen:
            continue
        chosen.add(k)
        s, e, _ = runs[k]
        for i in range(s, e):
            labels[i] = runs[k - 1][2]
    return labels


def _blank(w: dict[str, Any]) -> bool:
    return not str(w.get("text", "")).strip()


def _boundary_gap(words: list[dict[str, Any]], c: int) -> tuple[float, float] | None:
    """words[c-1] 與 words[c] 之間的停頓：回傳 (停頓秒數, 停頓中點時間)；
    左右任一邊（略過空白字）沒有真實時間戳就回傳 None。"""
    if c <= 0 or c >= len(words) or _blank(words[c]):
        return None
    j = c - 1
    while j >= 0 and _blank(words[j]):
        j -= 1
    if j < 0 or words[j].get("_interp") or words[c].get("_interp"):
        return None
    left_end = float(words[j]["end"])
    right_start = float(words[c]["start"])
    return max(0.0, right_start - left_end), (left_end + right_start) / 2


def align_boundaries(words: list[dict[str, Any]], labels: list[str | None]) -> list[str | None]:
    """換講者的邊界落在連續語音中（原邊界停頓 < BOUNDARY_MIN_PAUSE，涉及內插字視為 0）時，
    移到 ±BOUNDARY_SEARCH_SECONDS 內最大的詞間停頓。原邊界已有真實停頓就不動。
    只採用真實時間戳（沒有 `_interp` 旗標）的停頓；邊界只在相鄰兩個 run 內移動，
    兩個 run 都至少保留一個字，因此不會讓 run 消失或跨越其他變化點。"""
    labels = list(labels)
    runs = [list(r) for r in _runs(labels)]
    for k in range(len(runs) - 1):
        s0, b, lab0 = runs[k]
        _, e1, lab1 = runs[k + 1]
        if lab0 is None or lab1 is None:
            continue
        cur = _boundary_gap(words, b)
        if cur is not None and cur[0] >= BOUNDARY_MIN_PAUSE:
            continue
        t0 = (float(words[b - 1]["end"]) + float(words[b]["start"])) / 2
        best_c, best_gap, best_dist = None, 0.0, 0.0
        # 從 b 往左、往右逐字走，時間超出搜尋範圍就停（run k 至少留 words[s0]，run k+1 至少留 words[e1-1]）
        for rng in (range(b - 1, s0, -1), range(b + 1, e1)):
            for c in rng:
                g = _boundary_gap(words, c)
                tt = g[1] if g is not None else float(words[c]["start"])
                dist = abs(tt - t0)
                if dist > BOUNDARY_SEARCH_SECONDS:
                    break
                if g is None or g[0] < BOUNDARY_MIN_PAUSE:
                    continue
                if best_c is None or g[0] > best_gap or (g[0] == best_gap and dist < best_dist):
                    best_c, best_gap, best_dist = c, g[0], dist
        if best_c is None:
            continue
        for i in range(min(b, best_c), max(b, best_c)):
            labels[i] = lab0 if best_c > b else lab1
        runs[k][1] = best_c
        runs[k + 1][0] = best_c
    return labels


def assign_speakers(
    asr_segments: Sequence[dict[str, Any]], turns: Sequence[Turn] | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    turns = sorted(turns or [], key=lambda t: t[0])
    index = _TurnIndex(turns)
    last_speaker: str | None = None

    # 整份逐字稿的 word 串成一條序列（記錄所屬 ASR 段落），才能跨段落平滑與對齊邊界
    all_words: list[dict[str, Any]] = []
    all_labels: list[str | None] = []

    for seg_idx, seg in enumerate(asr_segments):
        words = fill_word_times(seg)
        if not words:
            continue
        if not turns:
            text = "".join(str(w["text"]) for w in words).strip()
            if text:
                out.append(
                    {
                        "start": float(words[0]["start"]),
                        "end": float(words[-1]["end"]),
                        "speaker": None,
                        "text": text,
                    }
                )
            continue

        labels: list[str | None] = []
        for w in words:
            if not str(w.get("text", "")).strip():
                labels.append(None)  # 空白字元：之後沿用前一個字的講者
                continue
            labels.append(index.lookup(float(w["start"]), float(w["end"])))
        # 沒有講者的字沿用前一個字（段首則沿用後一個字，再不行沿用上一段最後的講者）
        for i, lab in enumerate(labels):
            if lab is None and i > 0:
                labels[i] = labels[i - 1]
        for i in range(len(labels) - 2, -1, -1):
            if labels[i] is None:
                labels[i] = labels[i + 1]
        labels = [lab if lab is not None else last_speaker for lab in labels]
        if labels and labels[-1] is not None:
            last_speaker = labels[-1]
        for w in words:
            w["_seg"] = seg_idx
        all_words.extend(words)
        all_labels.extend(labels)

    if all_words:
        # 開頭沒對到任何講者區段（也沒有上一段可沿用）的字，沿用之後第一個有講者的字
        first = next((lab for lab in all_labels if lab is not None), None)
        for i, lab in enumerate(all_labels):
            if lab is not None:
                break
            all_labels[i] = first
        all_labels = smooth_labels(all_words, all_labels)
        all_labels = align_boundaries(all_words, all_labels)
        # 在 ASR 段落邊界或講者變化處切出輸出段落
        start = 0
        for i in range(1, len(all_words) + 1):
            if (
                i == len(all_words)
                or all_labels[i] != all_labels[start]
                or all_words[i]["_seg"] != all_words[start]["_seg"]
            ):
                chunk = all_words[start:i]
                text = "".join(str(w["text"]) for w in chunk).strip()
                if text:
                    # 時間只取非空白字（空白字的時間是內插的，會拉長 end 或造成 start > end）
                    solid = [w for w in chunk if str(w.get("text", "")).strip()]
                    out.append(
                        {
                            "start": min(float(w["start"]) for w in solid),
                            "end": max(float(w["end"]) for w in solid),
                            "speaker": all_labels[start],
                            "text": text,
                        }
                    )
                start = i

    for i, seg in enumerate(out):
        seg["id"] = i
    return out


def speaker_durations(turns: Sequence[Turn]) -> dict[str, float]:
    total: dict[str, float] = {}
    for t0, t1, spk in turns:
        total[spk] = total.get(spk, 0.0) + max(0.0, t1 - t0)
    return total

import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { Dictionary } from "../../types";

/** 改字存檔後出現在泡泡下方：「把『X』→『Y』加入字典？」；可以調整字詞、選字典。 */
export default function DictSuggest({
  dicts,
  initial,
  onClose,
  onAdded,
}: {
  dicts: Dictionary[];
  initial: { wrong: string; correct: string };
  onClose: () => void;
  onAdded: (d: Dictionary) => void;
}) {
  const [wrong, setWrong] = useState(initial.wrong);
  const [correct, setCorrect] = useState(initial.correct);
  const [adjust, setAdjust] = useState(false);
  const [dictId, setDictId] = useState<number | null>(() => {
    const last = Number(localStorage.getItem("v2t.lastDict"));
    return dicts.find((d) => d.id === last)?.id ?? dicts[0]?.id ?? null;
  });
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const dict = dicts.find((d) => d.id === dictId);

  const add = async () => {
    if (dictId === null) return;
    setBusy(true);
    setMsg(null);
    try {
      const d = await api.addRule(dictId, wrong.trim(), correct.trim());
      localStorage.setItem("v2t.lastDict", String(dictId));
      onAdded(d);
      setMsg({ text: `已加入「${d.name}」`, error: false });
      window.setTimeout(onClose, 1200);
    } catch (e) {
      setMsg({ text: (e as Error).message, error: true });
      setBusy(false);
    }
  };

  return (
    <div className="suggest" role="group" aria-label="加入字典">
      {adjust ? (
        <div className="suggest-edit">
          <input className="field" value={wrong} onChange={(e) => setWrong(e.target.value)} aria-label="錯字" />
          <span aria-hidden="true">→</span>
          <input className="field" value={correct} onChange={(e) => setCorrect(e.target.value)} aria-label="正字" />
        </div>
      ) : (
        <span className="grow">
          把<q>『{wrong}』</q>→<q>『{correct}』</q>加入字典？
        </span>
      )}
      {dicts.length === 0 ? (
        <span className="help">
          還沒有字典，先到<Link to="/dictionaries">字典</Link>新增一份。
        </span>
      ) : (
        <span className="suggest-acts">
          {dicts.length > 1 && (
            <select className="field" value={dictId ?? ""} onChange={(e) => setDictId(Number(e.target.value))} aria-label="字典">
              {dicts.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="tbtn ink"
            disabled={busy || !wrong.trim() || !correct.trim()}
            onClick={add}
          >
            {dicts.length > 1 ? "加入" : `加入「${dict?.name ?? ""}」`}
          </button>
          {!adjust && (
            <button type="button" className="tbtn" onClick={() => setAdjust(true)}>
              調整字詞
            </button>
          )}
          <button type="button" className="tbtn" onClick={onClose}>
            略過
          </button>
        </span>
      )}
      {msg && (
        <span className={`suggest-msg${msg.error ? " error" : ""}`} role={msg.error ? "alert" : "status"}>
          {msg.text}
        </span>
      )}
    </div>
  );
}

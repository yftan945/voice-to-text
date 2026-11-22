import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { useT } from "../../i18n";
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
  const t = useT();
  const [wrong, setWrong] = useState(initial.wrong);
  const [correct, setCorrect] = useState(initial.correct);
  const [adjust, setAdjust] = useState(false);
  const [dictId, setDictId] = useState<number | null>(() => {
    let last = NaN;
    try {
      last = Number(localStorage.getItem("v2t.lastDict"));
    } catch {
      /* 讀不到（無痕模式等）就用第一本 */
    }
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
      try {
        localStorage.setItem("v2t.lastDict", String(dictId));
      } catch {
        /* 記不住上次選的字典不影響加入 */
      }
      onAdded(d);
      setMsg({ text: t("sug.added", { name: d.name }), error: false });
      window.setTimeout(onClose, 1200);
    } catch (e) {
      setMsg({ text: (e as Error).message, error: true });
      setBusy(false);
    }
  };

  return (
    <div className="suggest" role="group" aria-label={t("sug.label")}>
      {adjust ? (
        <div className="suggest-edit">
          <input className="field" value={wrong} onChange={(e) => setWrong(e.target.value)} aria-label={t("sug.wrong")} />
          <span aria-hidden="true">→</span>
          <input className="field" value={correct} onChange={(e) => setCorrect(e.target.value)} aria-label={t("sug.correct")} />
        </div>
      ) : (
        <span className="grow">
          {t("sug.before")}
          <q>
            {t("sug.quoteOpen")}
            {wrong}
            {t("sug.quoteClose")}
          </q>
          {t("sug.arrow")}
          <q>
            {t("sug.quoteOpen")}
            {correct}
            {t("sug.quoteClose")}
          </q>
          {t("sug.after")}
        </span>
      )}
      {dicts.length === 0 ? (
        <span className="help">
          {t("sug.noDicts.before")}
          <Link to="/dictionaries">{t("sug.noDicts.link")}</Link>
          {t("sug.noDicts.after")}
        </span>
      ) : (
        <span className="suggest-acts">
          {dicts.length > 1 && (
            <select className="field" value={dictId ?? ""} onChange={(e) => setDictId(Number(e.target.value))} aria-label={t("sug.dict")}>
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
            {dicts.length > 1 ? t("sug.add") : t("sug.addTo", { name: dict?.name ?? "" })}
          </button>
          {!adjust && (
            <button type="button" className="tbtn" onClick={() => setAdjust(true)}>
              {t("sug.adjust")}
            </button>
          )}
          <button type="button" className="tbtn" onClick={onClose}>
            {t("sug.skip")}
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

import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, loadModels } from "../api";
import type { Dictionary, JobOptionsValue, ModelList } from "../types";
import Tick from "./Tick";

const MAX_SPEAKERS = 20;

/** 在名稱的括號裡補註記：「精準（Breeze-ASR-25）」＋預設 →「精準（Breeze-ASR-25，預設）」；沒有括號就加一組。 */
function withNotes(label: string, notes: string[]): string {
  if (!notes.length) return label;
  const n = notes.join("，");
  return label.endsWith("）") ? `${label.slice(0, -1)}，${n}）` : `${label}（${n}）`;
}

/** 首頁（整批共用）與重新轉錄共用的選項：辨識模型、區分講者與人數、字典（可複選）。 */
export default function JobOptionsForm({
  value,
  onChange,
  disabled = false,
}: {
  value: JobOptionsValue;
  onChange: (v: JobOptionsValue) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const [models, setModels] = useState<ModelList | null>(null);
  const [modelsError, setModelsError] = useState("");
  const [dicts, setDicts] = useState<Dictionary[] | null>(null);
  const [dictsError, setDictsError] = useState("");
  // 載入完成時用最新的值修正（兩個請求各自回來；先改的結果要讓後改的看得到，才不會互蓋）
  const valueRef = useRef(value);
  valueRef.current = value;
  const patchLatest = (patch: Partial<JobOptionsValue>) => {
    const next = { ...valueRef.current, ...patch };
    valueRef.current = next;
    onChange(next);
  };

  useEffect(() => {
    loadModels().then(
      (m) => {
        setModels(m);
        if (!valueRef.current.model) {
          const def = m.models.find((x) => x.id === m.default && x.available) ?? m.models.find((x) => x.available);
          if (def) patchLatest({ model: def.id });
        }
      },
      (e) => setModelsError((e as Error).message),
    );
    api.dictionaries().then(
      (list) => {
        setDicts(list);
        // 已經被刪除的字典（例如排隊中任務原本選的）從選擇裡拿掉，只呼叫一次 onChange
        const ids = new Set(list.map((d) => d.id));
        const cur = valueRef.current.dictionaryIds;
        const kept = cur.filter((i) => ids.has(i));
        if (kept.length !== cur.length) patchLatest({ dictionaryIds: kept });
      },
      (e) => setDictsError((e as Error).message),
    );
  }, []);

  const set = (patch: Partial<JobOptionsValue>) => onChange({ ...value, ...patch });
  const toggleDict = (did: number) =>
    set({
      dictionaryIds: value.dictionaryIds.includes(did)
        ? value.dictionaryIds.filter((x) => x !== did)
        : [...value.dictionaryIds, did],
    });
  const current = models?.models.find((m) => m.id === value.model);

  return (
    <fieldset className="opts" disabled={disabled}>
      <legend className="sr-only">轉錄設定</legend>
      <div className="opt-model">
        <label className="lab" htmlFor={`${id}-model`}>
          辨識模型{current && <span className="d">{current.description}</span>}
        </label>
        {modelsError ? (
          <p className="err">無法載入模型清單：{modelsError}</p>
        ) : (
          <select
            id={`${id}-model`}
            className="field"
            value={value.model}
            disabled={models === null}
            onChange={(e) => set({ model: e.target.value })}
          >
            {models === null && <option value="">載入中…</option>}
            {models?.models.map((m) => (
              <option key={m.id} value={m.id} disabled={!m.available}>
                {withNotes(m.label, [
                  ...(m.id === models.default ? ["預設"] : []),
                  ...(!m.available ? ["尚未安裝"] : []),
                ])}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="opt-spk">
        <Tick on={value.diarize} onChange={(v) => set({ diarize: v })}>
          區分講者
        </Tick>
        {value.diarize && (
          <label className="opt-num">
            <span className="sr-only">講者人數</span>
            <select
              className="field"
              value={value.numSpeakers === null ? "auto" : String(value.numSpeakers)}
              onChange={(e) => set({ numSpeakers: e.target.value === "auto" ? null : Number(e.target.value) })}
              aria-label="講者人數"
            >
              <option value="auto">自動判斷人數</option>
              {Array.from({ length: MAX_SPEAKERS }, (_, k) => k + 1).map((n) => (
                <option key={n} value={n}>
                  {n} 人
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="opt-dict" role="group" aria-labelledby={`${id}-dict`}>
        <span className="lab" id={`${id}-dict`}>
          字典<span className="d">可複選；辨識前當提示詞，辨識後自動替換誤聽</span>
        </span>
        {dictsError ? (
          <p className="err">無法載入字典：{dictsError}</p>
        ) : dicts === null ? (
          <p className="help">載入中…</p>
        ) : dicts.length === 0 ? (
          <p className="help">
            還沒有字典。可以到<Link to="/dictionaries">字典</Link>新增常用的專有名詞。
          </p>
        ) : (
          <div className="opt-dicts">
            {dicts.map((d) => (
              <Tick key={d.id} on={value.dictionaryIds.includes(d.id)} onChange={() => toggleDict(d.id)}>
                {d.name}
                <small className="num">
                  {d.term_count} 詞，{d.rule_count} 條替換
                </small>
              </Tick>
            ))}
          </div>
        )}
      </div>
    </fieldset>
  );
}

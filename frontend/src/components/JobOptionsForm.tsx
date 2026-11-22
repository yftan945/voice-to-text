import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, loadModels } from "../api";
import { modelText } from "../hooks";
import { useLang, useT } from "../i18n";
import type { Dictionary, JobOptionsValue, ModelList } from "../types";
import Tick from "./Tick";

const MAX_SPEAKERS = 20;

/**
 * 在名稱的括號裡補註記：「精準（Breeze-ASR-25）」＋預設 →「精準（Breeze-ASR-25，預設）」，
 * 英文 "Accurate (Breeze-ASR-25)" → "Accurate (Breeze-ASR-25, default)"；沒有括號就加一組（括號跟著介面語言）。
 */
function withNotes(label: string, notes: string[], en: boolean): string {
  if (!notes.length) return label;
  if (label.endsWith("）")) return `${label.slice(0, -1)}，${notes.join("，")}）`;
  if (label.endsWith(")")) return `${label.slice(0, -1)}, ${notes.join(", ")})`;
  return en ? `${label} (${notes.join(", ")})` : `${label}（${notes.join("，")}）`;
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
  const t = useT();
  const lang = useLang();
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

  // 模型清單：換語言時重新讀（後端依語言回傳名稱與說明）
  useEffect(() => {
    let alive = true;
    loadModels().then(
      (m) => {
        if (!alive) return;
        setModels(m);
        setModelsError("");
        if (!valueRef.current.model) {
          const def = m.models.find((x) => x.id === m.default && x.available) ?? m.models.find((x) => x.available);
          if (def) patchLatest({ model: def.id });
        }
      },
      (e) => alive && setModelsError((e as Error).message),
    );
    return () => {
      alive = false;
    };
  }, [lang]);

  useEffect(() => {
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
      <legend className="sr-only">{t("opts.legend")}</legend>
      <div className="opt-model">
        <label className="lab" htmlFor={`${id}-model`}>
          {t("opts.model")}
          {current && <span className="d">{modelText(current).description}</span>}
        </label>
        {modelsError ? (
          <p className="err">{t("opts.modelsError", { msg: modelsError })}</p>
        ) : (
          <select
            id={`${id}-model`}
            className="field"
            value={value.model}
            disabled={models === null}
            onChange={(e) => set({ model: e.target.value })}
          >
            {models === null && <option value="">{t("common.loading")}</option>}
            {models?.models.map((m) => (
              <option key={m.id} value={m.id} disabled={!m.available}>
                {withNotes(
                  modelText(m).label,
                  [
                    ...(m.id === models.default ? [t("opts.default")] : []),
                    ...(!m.available ? [t("opts.notInstalled")] : []),
                  ],
                  lang === "en",
                )}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="opt-spk">
        <Tick on={value.diarize} onChange={(v) => set({ diarize: v })}>
          {t("opts.diarize")}
        </Tick>
        {value.diarize && (
          <label className="opt-num">
            <span className="sr-only">{t("opts.numSpeakers")}</span>
            <select
              className="field"
              value={value.numSpeakers === null ? "auto" : String(value.numSpeakers)}
              onChange={(e) => set({ numSpeakers: e.target.value === "auto" ? null : Number(e.target.value) })}
              aria-label={t("opts.numSpeakers")}
            >
              <option value="auto">{t("opts.autoCount")}</option>
              {Array.from({ length: MAX_SPEAKERS }, (_, k) => k + 1).map((n) => (
                <option key={n} value={n}>
                  {t("opts.nPeople", { n })}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="opt-dict" role="group" aria-labelledby={`${id}-dict`}>
        <span className="lab" id={`${id}-dict`}>
          {t("opts.dict")}
          <span className="d">{t("opts.dictHint")}</span>
        </span>
        {dictsError ? (
          <p className="err">{t("opts.dictsError", { msg: dictsError })}</p>
        ) : dicts === null ? (
          <p className="help">{t("common.loading")}</p>
        ) : dicts.length === 0 ? (
          <p className="help">
            {t("opts.noDicts.before")}
            <Link to="/dictionaries">{t("opts.noDicts.link")}</Link>
            {t("opts.noDicts.after")}
          </p>
        ) : (
          <div className="opt-dicts">
            {dicts.map((d) => (
              <Tick key={d.id} on={value.dictionaryIds.includes(d.id)} onChange={() => toggleDict(d.id)}>
                {d.name}
                <small className="num">{t("opts.dictCounts", { terms: d.term_count, rules: d.rule_count })}</small>
              </Tick>
            ))}
          </div>
        )}
      </div>
    </fieldset>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ConfirmModal } from "../components/Modal";
import { dateTime } from "../format";
import { useUnsavedBlocker } from "../hooks";
import { useLang, useT } from "../i18n";
import type { Dictionary } from "../types";

export default function DictionariesPage() {
  const t = useT();
  const lang = useLang();
  const example = t("dict.example");
  const [dicts, setDicts] = useState<Dictionary[] | null>(null);
  const [selected, setSelected] = useState<number | "new" | null>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  // 開啟時的內容，用來判斷有沒有未儲存的修改
  const [saved, setSaved] = useState({ name: "", content: "" });
  // 儲存成功的提示（存成旗標，顯示時才翻譯，換語言時跟著換）
  const [savedMsg, setSavedMsg] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingOpen, setPendingOpen] = useState<Dictionary | "new" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const dirty = selected !== null && (name !== saved.name || content !== saved.content);
  // 關閉分頁、重新整理、站內換頁前都先確認
  const blocker = useUnsavedBlocker(dirty);

  // 本頁新增、修改、刪除完成的次數：讀取途中有過這些操作，讀回來的清單可能是舊的（會蓋掉剛儲存的結果），丟掉重讀
  const mutations = useRef(0);
  // 換語言時重新讀（讀取錯誤訊息跟著換）；換頁或再換語言後晚到的舊回應不套用
  useEffect(() => {
    let alive = true;
    const load = () => {
      const at = mutations.current;
      api.dictionaries().then(
        (l) => {
          if (!alive) return;
          if (at !== mutations.current) return load();
          setDicts(l);
        },
        (e) => {
          if (alive) setError(e.message);
        },
      );
    };
    load();
    return () => {
      alive = false;
    };
  }, [lang]);

  const doOpen = (d: Dictionary | "new") => {
    setSavedMsg(false);
    setError("");
    if (d === "new") {
      setSelected("new");
      setName("");
      setContent("");
      setSaved({ name: "", content: "" });
    } else {
      setSelected(d.id);
      setName(d.name);
      setContent(d.content);
      setSaved({ name: d.name, content: d.content });
    }
  };

  const open = (d: Dictionary | "new") => {
    if (d !== "new" && d.id === selected) return;
    if (dirty) setPendingOpen(d);
    else doOpen(d);
  };

  const save = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    setSavedMsg(false);
    try {
      let d: Dictionary | null = null;
      if (selected === "new") {
        d = await api.createDictionary(name.trim(), content);
        mutations.current += 1;
        const created = d;
        setDicts((prev) => [...(prev ?? []), created].sort((a, b) => a.name.localeCompare(b.name)));
        setSelected(d.id);
      } else if (selected !== null) {
        d = await api.updateDictionary(selected, { name: name.trim(), content });
        mutations.current += 1;
        const updated = d;
        setDicts((prev) => prev?.map((x) => (x.id === updated.id ? updated : x)) ?? prev);
      }
      if (d) {
        setName(d.name);
        setContent(d.content);
        setSaved({ name: d.name, content: d.content });
      }
      setSavedMsg(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (typeof selected !== "number") return;
    await api.deleteDictionary(selected); // 失敗時錯誤顯示在確認對話框裡
    mutations.current += 1;
    setDicts((prev) => prev?.filter((x) => x.id !== selected) ?? prev);
    setSelected(null);
    setSaved({ name: "", content: "" });
    setName("");
    setContent("");
    setConfirmDelete(false);
  };

  return (
    <main className="page lib">
      <h1>{t("nav.dictionaries")}</h1>
      <p className="lead">{t("dict.lead")}</p>
      {error && (
        <p className="note error-note" role="alert">
          {error}
        </p>
      )}
      <div className="split">
        <nav className="split-list" aria-label={t("dict.listLabel")}>
          <button type="button" className="btn primary" onClick={() => open("new")}>
            {t("dict.new")}
          </button>
          {dicts === null ? (
            !error && <p className="empty">{t("common.loading")}</p>
          ) : dicts.length === 0 ? (
            <p className="empty">{t("dict.empty")}</p>
          ) : (
            <ul className="list">
              {dicts.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    className={`list-item${selected === d.id ? " on" : ""}`}
                    aria-current={selected === d.id ? "true" : undefined}
                    onClick={() => open(d)}
                  >
                    <span className="li-name">{d.name}</span>
                    <small className="num">
                      {t("opts.dictCounts", { terms: d.term_count, rules: d.rule_count })}
                      {t("common.sep")}
                      {dateTime(d.updated_at)}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>
        <section className="split-main" aria-label={t("dict.contentLabel")}>
          {selected === null ? (
            <div className="syntax">
              <p className="muted">{t("dict.pick")}</p>
              <h2>{t("dict.syntax")}</h2>
              <pre className="example">{example}</pre>
            </div>
          ) : (
            <div className="editor-form">
              <label className="lab" htmlFor="dict-name">
                {t("common.name")}
              </label>
              <input
                id="dict-name"
                className="field"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("dict.namePlaceholder")}
              />
              <label className="lab" htmlFor="dict-content">
                {t("dict.content")}
              </label>
              <textarea
                id="dict-content"
                className="field mono"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={16}
                placeholder={example}
                spellCheck={false}
              />
              <p className="help">
                {t("dict.help1")}
                <code>{t("dict.helpRule")}</code>
                {t("dict.help2")}
                <code>#</code>
                {t("dict.help3")}
              </p>
              <div className="form-acts">
                {typeof selected === "number" && (
                  <button type="button" className="btn danger" disabled={busy} onClick={() => setConfirmDelete(true)}>
                    {t("common.delete")}
                  </button>
                )}
                <span className={`status${dirty ? " dirty" : ""}`} role="status">
                  {dirty ? t("dict.unsaved") : savedMsg ? t("common.saved") : ""}
                </span>
                <button type="button" className="btn primary" disabled={busy || !name.trim()} onClick={save}>
                  {busy ? t("common.saving") : t("common.save")}
                </button>
              </div>
            </div>
          )}
        </section>
      </div>

      {pendingOpen && (
        <ConfirmModal
          title={t("common.discardTitle")}
          message={t("dict.discardMsg")}
          confirmLabel={t("common.discard")}
          cancelLabel={t("common.keepEditing")}
          danger
          onClose={() => setPendingOpen(null)}
          onConfirm={() => {
            const d = pendingOpen;
            setPendingOpen(null);
            doOpen(d);
          }}
        />
      )}
      {blocker.state === "blocked" && (
        <ConfirmModal
          title={t("common.discardTitle")}
          message={t("dict.leaveMsg")}
          confirmLabel={t("common.leave")}
          cancelLabel={t("common.keepEditing")}
          danger
          onClose={() => blocker.reset()}
          onConfirm={() => blocker.proceed()}
        />
      )}
      {confirmDelete && (
        <ConfirmModal
          title={t("dict.deleteTitle")}
          message={t("dict.deleteMsg", { name: saved.name || name })}
          confirmLabel={t("common.delete")}
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={remove}
        />
      )}
    </main>
  );
}

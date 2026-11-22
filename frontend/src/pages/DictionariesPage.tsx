import { useEffect, useState } from "react";
import { api } from "../api";
import { ConfirmModal } from "../components/Modal";
import { dateTime } from "../format";
import { useUnsavedBlocker } from "../hooks";
import type { Dictionary } from "../types";

const EXAMPLE = `# 每行一個詞（只當辨識提示）
Kubernetes
# 正字 = 誤聽1, 誤聽2（提示＋辨識後自動替換）
人工智慧 = 人工只能, 人工智能
OpenAI = open AI, 偶噴AI`;

export default function DictionariesPage() {
  const [dicts, setDicts] = useState<Dictionary[] | null>(null);
  const [selected, setSelected] = useState<number | "new" | null>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  // 開啟時的內容，用來判斷有沒有未儲存的修改
  const [saved, setSaved] = useState({ name: "", content: "" });
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingOpen, setPendingOpen] = useState<Dictionary | "new" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const dirty = selected !== null && (name !== saved.name || content !== saved.content);
  // 關閉分頁、重新整理、站內換頁前都先確認
  const blocker = useUnsavedBlocker(dirty);

  useEffect(() => {
    api.dictionaries().then(setDicts, (e) => setError(e.message));
  }, []);

  const doOpen = (d: Dictionary | "new") => {
    setMsg("");
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
    setMsg("");
    try {
      let d: Dictionary | null = null;
      if (selected === "new") {
        d = await api.createDictionary(name.trim(), content);
        const created = d;
        setDicts((prev) => [...(prev ?? []), created].sort((a, b) => a.name.localeCompare(b.name)));
        setSelected(d.id);
      } else if (selected !== null) {
        d = await api.updateDictionary(selected, { name: name.trim(), content });
        const updated = d;
        setDicts((prev) => prev?.map((x) => (x.id === updated.id ? updated : x)) ?? prev);
      }
      if (d) {
        setName(d.name);
        setContent(d.content);
        setSaved({ name: d.name, content: d.content });
      }
      setMsg("已儲存");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (typeof selected !== "number") return;
    await api.deleteDictionary(selected); // 失敗時錯誤顯示在確認對話框裡
    setDicts((prev) => prev?.filter((x) => x.id !== selected) ?? prev);
    setSelected(null);
    setSaved({ name: "", content: "" });
    setName("");
    setContent("");
    setConfirmDelete(false);
  };

  return (
    <main className="page lib">
      <h1>字典</h1>
      <p className="lead">
        字典有兩個作用：辨識前當作提示詞，讓模型更容易聽對專有名詞；辨識後把常見的誤聽自動換成正字。新增錄音時可以選一份或多份。
      </p>
      {error && (
        <p className="note error-note" role="alert">
          {error}
        </p>
      )}
      <div className="split">
        <nav className="split-list" aria-label="字典清單">
          <button type="button" className="btn primary" onClick={() => open("new")}>
            新增字典
          </button>
          {dicts === null ? (
            !error && <p className="empty">載入中…</p>
          ) : dicts.length === 0 ? (
            <p className="empty">還沒有字典。</p>
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
                      {d.term_count} 詞，{d.rule_count} 條替換，{dateTime(d.updated_at)}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>
        <section className="split-main" aria-label="字典內容">
          {selected === null ? (
            <div className="syntax">
              <p className="muted">選擇一份字典編輯，或新增一份。</p>
              <h2>語法</h2>
              <pre className="example">{EXAMPLE}</pre>
            </div>
          ) : (
            <div className="editor-form">
              <label className="lab" htmlFor="dict-name">
                名稱
              </label>
              <input
                id="dict-name"
                className="field"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：公司產品名稱"
              />
              <label className="lab" htmlFor="dict-content">
                內容
              </label>
              <textarea
                id="dict-content"
                className="field mono"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={16}
                placeholder={EXAMPLE}
                spellCheck={false}
              />
              <p className="help">
                每行一個詞；<code>正字 = 誤聽1, 誤聽2</code> 表示替換規則；<code>#</code> 開頭為註解。
              </p>
              <div className="form-acts">
                {typeof selected === "number" && (
                  <button type="button" className="btn danger" disabled={busy} onClick={() => setConfirmDelete(true)}>
                    刪除
                  </button>
                )}
                <span className={`status${dirty ? " dirty" : ""}`} role="status">
                  {dirty ? "有未儲存的修改" : msg}
                </span>
                <button type="button" className="btn primary" disabled={busy || !name.trim()} onClick={save}>
                  {busy ? "儲存中…" : "儲存"}
                </button>
              </div>
            </div>
          )}
        </section>
      </div>

      {pendingOpen && (
        <ConfirmModal
          title="放棄修改？"
          message="這份字典還沒儲存，要放棄修改嗎？"
          confirmLabel="放棄修改"
          cancelLabel="繼續編輯"
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
          title="放棄修改？"
          message="這份字典還沒儲存，離開這個頁面會放棄修改。"
          confirmLabel="離開"
          cancelLabel="繼續編輯"
          danger
          onClose={() => blocker.reset()}
          onConfirm={() => blocker.proceed()}
        />
      )}
      {confirmDelete && (
        <ConfirmModal
          title="刪除字典"
          message={`確定刪除字典「${saved.name || name}」？刪除後無法復原。`}
          confirmLabel="刪除"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={remove}
        />
      )}
    </main>
  );
}

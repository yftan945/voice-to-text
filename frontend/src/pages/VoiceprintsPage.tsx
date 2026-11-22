import { useEffect, useState } from "react";
import { api } from "../api";
import FileDropzone from "../components/FileDropzone";
import { ConfirmModal, PromptModal } from "../components/Modal";
import { dateTime } from "../format";
import { useIsMobile } from "../hooks";
import type { Voiceprint } from "../types";

export default function VoiceprintsPage() {
  const isMobile = useIsMobile();
  const [list, setList] = useState<Voiceprint[] | null>(null);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [renaming, setRenaming] = useState<Voiceprint | null>(null);
  const [deleting, setDeleting] = useState<Voiceprint | null>(null);

  useEffect(() => {
    api.voiceprints().then(setList, (e) => setListError(e.message));
  }, []);

  const enroll = async () => {
    if (!file || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const vp = await api.enrollVoiceprint(name.trim(), file);
      setList((prev) => [...(prev ?? []), vp]);
      setName("");
      setFile(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page lib">
      <h1>聲紋庫</h1>
      <p className="lead">
        登記過的聲紋會在轉錄時自動比對，相似度夠高的講者會直接標上名字；信心不足時顯示成「講者 1」「講者 2」。也可以在結果頁的講者清單按「存成聲紋」。
      </p>

      <section className="sec" aria-labelledby="enroll-title">
        <div className="sec-head">
          <h2 id="enroll-title">上傳樣本登記</h2>
          <span className="sub">10–30 秒、只有這個人說話的錄音（超過 30 秒只取前 30 秒）</span>
        </div>
        <div className="enroll">
          <div>
            <label className="lab" htmlFor="vp-name">
              名稱
            </label>
            <input
              id="vp-name"
              className="field"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：Alex"
            />
          </div>
          <FileDropzone
            file={file}
            disabled={busy}
            compact
            onPick={(f) => {
              setFile(f);
              setError("");
            }}
            title={isMobile ? "選擇樣本錄音檔" : "把樣本錄音拖到這裡"}
            hint="10–30 秒的單人語音；支援 m4a、mp3、wav、iPhone 語音備忘錄（.qta）等"
          />
          <div className="form-acts">
            {error && (
              <span className="err" role="alert">
                {error}
              </span>
            )}
            <span className="grow" />
            <button type="button" className="btn primary" disabled={busy || !file || !name.trim()} onClick={enroll}>
              {busy ? "計算聲紋中…" : "登記"}
            </button>
          </div>
        </div>
      </section>

      <section className="sec" aria-labelledby="vp-title">
        <div className="sec-head">
          <h2 id="vp-title">已登記的聲紋</h2>
          {list && <span className="sub num">{list.length} 筆</span>}
        </div>
        {listError ? (
          <p className="err">{listError}</p>
        ) : list === null ? (
          <p className="empty">載入中…</p>
        ) : list.length === 0 ? (
          <p className="empty">還沒有登記任何聲紋。</p>
        ) : (
          <ul className="vp-list">
            {list.map((vp) => (
              <li key={vp.id} className="vp">
                <div className="vp-main">
                  <span className="vp-name">{vp.name}</span>
                  <small className="num">
                    {vp.source === "upload" ? "上傳樣本" : "從逐字稿存入"}
                    {vp.duration ? `，${vp.duration.toFixed(0)} 秒` : ""}，{dateTime(vp.created_at)}
                  </small>
                  {!vp.compatible && <small className="warn-text">聲紋模型已更換，這筆不會被比對，請重新登記</small>}
                </div>
                <div className="vp-acts">
                  <button type="button" className="tbtn" onClick={() => setRenaming(vp)}>
                    改名
                  </button>
                  <button type="button" className="tbtn danger" onClick={() => setDeleting(vp)}>
                    刪除
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {renaming && (
        <PromptModal
          title="聲紋改名"
          label="新名稱"
          initial={renaming.name}
          confirmLabel="儲存"
          onClose={() => setRenaming(null)}
          onSubmit={async (n) => {
            await api.renameVoiceprint(renaming.id, n);
            setList((prev) => prev?.map((x) => (x.id === renaming.id ? { ...x, name: n } : x)) ?? prev);
            setRenaming(null);
          }}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="刪除聲紋"
          message={`確定刪除聲紋「${deleting.name}」？之後轉錄不會再自動認出這個人。`}
          confirmLabel="刪除"
          danger
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api.deleteVoiceprint(deleting.id);
            setList((prev) => prev?.filter((x) => x.id !== deleting.id) ?? prev);
            setDeleting(null);
          }}
        />
      )}
    </main>
  );
}

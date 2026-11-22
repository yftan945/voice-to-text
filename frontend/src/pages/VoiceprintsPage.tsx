import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import FileDropzone from "../components/FileDropzone";
import { ConfirmModal, PromptModal } from "../components/Modal";
import { dateTime } from "../format";
import { useIsMobile } from "../hooks";
import { useLang, useT } from "../i18n";
import type { Voiceprint } from "../types";

export default function VoiceprintsPage() {
  const t = useT();
  const lang = useLang();
  const isMobile = useIsMobile();
  const [list, setList] = useState<Voiceprint[] | null>(null);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [renaming, setRenaming] = useState<Voiceprint | null>(null);
  const [deleting, setDeleting] = useState<Voiceprint | null>(null);

  // 本頁新增、改名、刪除完成的次數：讀取途中有過這些操作，讀回來的清單可能是舊的（會蓋掉剛儲存的結果），丟掉重讀
  const mutations = useRef(0);
  // 換語言時重新讀（讀取錯誤訊息跟著換）；換頁或再換語言後晚到的舊回應不套用
  useEffect(() => {
    let alive = true;
    const load = () => {
      const at = mutations.current;
      api.voiceprints().then(
        (l) => {
          if (!alive) return;
          if (at !== mutations.current) return load();
          setList(l);
          setListError("");
        },
        (e) => {
          if (alive) setListError(e.message);
        },
      );
    };
    load();
    return () => {
      alive = false;
    };
  }, [lang]);

  const enroll = async () => {
    if (!file || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const vp = await api.enrollVoiceprint(name.trim(), file);
      mutations.current += 1;
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
      <h1>{t("nav.voiceprints")}</h1>
      <p className="lead">{t("vp.lead")}</p>

      <section className="sec" aria-labelledby="enroll-title">
        <div className="sec-head">
          <h2 id="enroll-title">{t("vp.enrollTitle")}</h2>
          <span className="sub">{t("vp.enrollSub")}</span>
        </div>
        <div className="enroll">
          <div>
            <label className="lab" htmlFor="vp-name">
              {t("common.name")}
            </label>
            <input
              id="vp-name"
              className="field"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("vp.namePlaceholder")}
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
            title={isMobile ? t("vp.dropTitleMobile") : t("vp.dropTitle")}
            hint={t("vp.dropHint")}
          />
          <div className="form-acts">
            {error && (
              <span className="err" role="alert">
                {error}
              </span>
            )}
            <span className="grow" />
            <button type="button" className="btn primary" disabled={busy || !file || !name.trim()} onClick={enroll}>
              {busy ? t("vp.enrolling") : t("vp.enroll")}
            </button>
          </div>
        </div>
      </section>

      <section className="sec" aria-labelledby="vp-title">
        <div className="sec-head">
          <h2 id="vp-title">{t("vp.listTitle")}</h2>
          {list && <span className="sub num">{t("vp.count", { n: list.length })}</span>}
        </div>
        {listError ? (
          <p className="err">{listError}</p>
        ) : list === null ? (
          <p className="empty">{t("common.loading")}</p>
        ) : list.length === 0 ? (
          <p className="empty">{t("vp.empty")}</p>
        ) : (
          <ul className="vp-list">
            {list.map((vp) => (
              <li key={vp.id} className="vp">
                <div className="vp-main">
                  <span className="vp-name">{vp.name}</span>
                  <small className="num">
                    {[
                      vp.source === "upload" ? t("vp.fromUpload") : t("vp.fromTranscript"),
                      vp.duration ? t("time.s", { s: Math.round(vp.duration) }) : "",
                      dateTime(vp.created_at),
                    ]
                      .filter(Boolean)
                      .join(t("common.sep"))}
                  </small>
                  {!vp.compatible && <small className="warn-text">{t("vp.incompatible")}</small>}
                </div>
                <div className="vp-acts">
                  <button type="button" className="tbtn" onClick={() => setRenaming(vp)}>
                    {t("common.rename")}
                  </button>
                  <button type="button" className="tbtn danger" onClick={() => setDeleting(vp)}>
                    {t("common.delete")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {renaming && (
        <PromptModal
          title={t("vp.renameTitle")}
          label={t("vp.newName")}
          initial={renaming.name}
          confirmLabel={t("common.save")}
          onClose={() => setRenaming(null)}
          onSubmit={async (n) => {
            await api.renameVoiceprint(renaming.id, n);
            mutations.current += 1;
            setList((prev) => prev?.map((x) => (x.id === renaming.id ? { ...x, name: n } : x)) ?? prev);
            setRenaming(null);
          }}
        />
      )}
      {deleting && (
        <ConfirmModal
          title={t("vp.deleteTitle")}
          message={t("vp.deleteMsg", { name: deleting.name })}
          confirmLabel={t("common.delete")}
          danger
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api.deleteVoiceprint(deleting.id);
            mutations.current += 1;
            setList((prev) => prev?.filter((x) => x.id !== deleting.id) ?? prev);
            setDeleting(null);
          }}
        />
      )}
    </main>
  );
}

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import BottomSheet from "../components/BottomSheet";
import FileDropzone, { AUDIO_ACCEPT } from "../components/FileDropzone";
import Icon from "../components/Icon";
import JobOptionsForm from "../components/JobOptionsForm";
import { ConfirmModal } from "../components/Modal";
import { useToast } from "../components/Toast";
import YoutubeInput, { type YoutubePick } from "../components/YoutubeInput";
import { modelText, useIsMobile, useModels, useUnsavedBlocker } from "../hooks";
import { useT } from "../i18n";
import { isActive, useJobs } from "../jobs";
import type { Dictionary, JobOptionsValue, ModelList } from "../types";
import DoneSection from "./home/DoneSection";
import UploadSection from "./home/UploadSection";
import { useUploader } from "./home/useUploader";

/** 手機：設定收在底部面板裡，開始轉錄前在清單下方列出這批會用的設定，可以直接去修改。 */
function SettingsSummary({
  opts,
  models,
  dicts,
  onEdit,
}: {
  opts: JobOptionsValue;
  models: ModelList | null;
  dicts: Dictionary[] | null;
  onEdit: () => void;
}) {
  const t = useT();
  const m = models?.models.find((x) => x.id === opts.model);
  const names = (dicts ?? []).filter((d) => opts.dictionaryIds.includes(d.id)).map((d) => d.name);
  const items = [
    // 「精準（Breeze-ASR-25）」→「精準模型」
    m ? t("home.modelShort", { name: modelText(m).short }) : t("home.modelLoading"),
    opts.diarize
      ? t("home.diarizeOn", { n: opts.numSpeakers ? t("opts.nPeople", { n: opts.numSpeakers }) : t("home.auto") })
      : t("home.diarizeOff"),
    names.length ? t("home.dicts", { names: names.join(t("common.listSep")) }) : t("home.noDicts"),
  ];
  return (
    <div className="up-settings">
      <span className="lab">{t("home.batchSettings")}</span>
      <ul>
        {items.map((x) => (
          <li key={x} className="break">
            {x}
          </li>
        ))}
      </ul>
      <button type="button" className="tbtn ink" onClick={onEdit} aria-label={t("home.editLabel")}>
        {t("home.edit")}
      </button>
    </div>
  );
}

/**
 * 首頁「錄音」：新增（拖放檔案、YouTube 網址、整批設定）、要上傳的清單、已完成。
 * 處理中與排隊中在「任務」頁（TasksPage）；加入佇列後用提示附「查看任務」連結。
 * 手機：拖放區收起，底部固定「選擇錄音檔／貼上網址／設定」，網址與設定各自一個底部面板。
 */
export default function HomePage() {
  const t = useT();
  const navigate = useNavigate();
  const toast = useToast();
  const isMobile = useIsMobile();
  const models = useModels();
  const { jobs, error, merge, remove } = useJobs();
  const [opts, setOpts] = useState<JobOptionsValue>({ model: "", diarize: true, numSpeakers: null, dictionaryIds: [] });
  const [sheet, setSheet] = useState<"url" | "set" | null>(null);
  const [goTo, setGoTo] = useState<string | null>(null);
  const [dicts, setDicts] = useState<Dictionary[] | null>(null);
  const pickRef = useRef<HTMLInputElement>(null);

  // 手機的設定摘要要顯示字典名稱
  useEffect(() => {
    if (!isMobile) return;
    api.dictionaries().then(setDicts, () => setDicts([]));
  }, [isMobile]);

  const up = useUploader((items, fresh) => {
    // 只算這一輪新加入的（部分失敗後重試時，之前已經加入的不再算一次）
    const added = fresh.length;
    if (!added) return;
    const failed = items.filter((i) => i.state === "failed").length;
    const viewTasks = { label: t("home.viewTasks"), to: "/tasks" };
    if (failed) {
      // 有失敗的留在清單上重試；成功的已在背景排隊，到任務頁看得到
      toast(t("home.queuedSomeFailed", { added, failed }), viewTasks);
    } else if (items.length === 1) {
      // 單一項目直接看它的進度
      setGoTo(`/jobs/${items[0].jobId}`);
      toast(t("home.queuedOne"), viewTasks);
    } else {
      // 多個項目從清單拿掉，進度在任務頁
      up.clearDone();
      toast(t("home.queuedN", { n: added }), viewTasks);
    }
  });

  // 上傳中關閉分頁會中斷上傳；站內換頁也先確認
  const blocker = useUnsavedBlocker(up.uploading);

  // 全部上傳完成後換頁（等 uploading 變回 false，換頁才不會被擋）
  useEffect(() => {
    if (goTo && !up.uploading) navigate(goTo);
  }, [goTo, up.uploading, navigate]);

  // 手機的底部固定列會蓋住提示：提示往上移
  useEffect(() => {
    if (!isMobile) return;
    document.documentElement.style.setProperty("--toast-b", "86px");
    return () => {
      document.documentElement.style.removeProperty("--toast-b");
    };
  }, [isMobile]);

  const addFiles = (files: File[]) => {
    up.addFiles(files);
    if (files.length) toast(t("home.filesAdded", { n: files.length }));
  };

  const addVideos = (videos: YoutubePick[], done: boolean) => {
    const n = up.addVideos(videos);
    toast(n ? t("home.videosAdded", { n }) : t("home.videosDup"));
    // 手機：全部處理完就關掉面板，回到清單看得到剛加入的影片（有錯誤或還有播放清單要勾選時留著）
    if (isMobile && done) setSheet(null);
  };

  const finished = jobs?.filter((j) => !isActive(j)) ?? [];
  const pendingCount = up.items.filter((i) => i.state === "pending").length;

  const urlInput = <YoutubeInput disabled={up.uploading} onAdd={addVideos} />;
  const optionsForm = <JobOptionsForm value={opts} onChange={setOpts} disabled={up.uploading} />;

  return (
    <main className={`page home${isMobile ? " with-dock" : ""}`}>
      <h1>{t("nav.record")}</h1>

      <section aria-label={t("home.addSection")} className="add-sec">
        {!isMobile && (
          <>
            <FileDropzone
              multiple
              disabled={up.uploading}
              onPickFiles={addFiles}
              title={t("home.dropTitle")}
              hint={t("home.dropHint")}
            />
            <div className="add-panel">
              {urlInput}
              {optionsForm}
            </div>
          </>
        )}
        {up.notice && <p className="help">{t(up.notice)}</p>}
      </section>

      <UploadSection
        items={up.items}
        opts={opts}
        uploading={up.uploading}
        onStart={() => void up.run(opts)}
        onStop={up.stop}
        onRetry={(key) => up.retry(key, opts)}
        onRemove={up.remove}
        onSpeakers={up.setSpeakers}
        settings={isMobile ? <SettingsSummary opts={opts} models={models} dicts={dicts} onEdit={() => setSheet("set")} /> : undefined}
      />

      {error && <p className="err" role="alert">{error}</p>}
      {jobs === null ? (
        !error && <p className="empty">{t("common.loading")}</p>
      ) : (
        <DoneSection jobs={finished} models={models} onReplace={(j) => merge([j])} onRemove={remove} />
      )}

      {isMobile && (
        <>
          <div className="dock">
            {/* 清單裡有等待處理的項目時，主要按鈕是清單下方的「開始轉錄」，這裡改成一般樣式 */}
            <button
              type="button"
              className={`btn pick${pendingCount ? "" : " primary"}`}
              disabled={up.uploading}
              onClick={() => pickRef.current?.click()}
            >
              <Icon name="upload" />
              {t("home.dockPick")}
            </button>
            <button type="button" className="btn" onClick={() => setSheet("url")}>
              {t("home.dockUrl")}
            </button>
            <button type="button" className="btn" onClick={() => setSheet("set")}>
              {t("home.dockSettings")}
            </button>
          </div>
          <input
            ref={pickRef}
            type="file"
            accept={AUDIO_ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              addFiles(e.target.files ? Array.from(e.target.files) : []);
              e.target.value = "";
            }}
          />
          {/* 兩個面板一直掛著（關閉時 inert），輸入到一半的網址、選好的設定不會因為關閉而消失 */}
          <BottomSheet open={sheet === "url"} title={t("home.sheetUrl")} initialFocus="#yt-urls" onClose={() => setSheet(null)}>
            <div className="add-panel">{urlInput}</div>
          </BottomSheet>
          <BottomSheet open={sheet === "set"} title={t("opts.legend")} initialFocus=".opts select" onClose={() => setSheet(null)}>
            <div className="add-panel">{optionsForm}</div>
          </BottomSheet>
        </>
      )}

      {blocker.state === "blocked" && (
        <ConfirmModal
          title={t("home.leaveTitle")}
          message={t("home.leaveMsg")}
          confirmLabel={t("home.leaveConfirm")}
          cancelLabel={t("home.leaveCancel")}
          danger
          onClose={() => blocker.reset()}
          onConfirm={() => {
            up.stop();
            blocker.proceed();
          }}
        />
      )}
    </main>
  );
}

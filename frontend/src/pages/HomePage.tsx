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
import { useIsMobile, useModels, useUnsavedBlocker } from "../hooks";
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
  const m = models?.models.find((x) => x.id === opts.model);
  const names = (dicts ?? []).filter((d) => opts.dictionaryIds.includes(d.id)).map((d) => d.name);
  const items = [
    // 「精準（Breeze-ASR-25）」→「精準模型」
    m ? `${m.label.split("（")[0]}模型` : "模型載入中",
    opts.diarize ? `區分講者（${opts.numSpeakers ? `${opts.numSpeakers} 人` : "自動"}）` : "不分講者",
    names.length ? `字典：${names.join("、")}` : "不用字典",
  ];
  return (
    <div className="up-settings">
      <span className="lab">這批的設定</span>
      <ul>
        {items.map((x) => (
          <li key={x} className="break">
            {x}
          </li>
        ))}
      </ul>
      <button type="button" className="tbtn ink" onClick={onEdit} aria-label="修改轉錄設定">
        修改
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
    const viewTasks = { label: "查看任務", to: "/tasks" };
    if (failed) {
      // 有失敗的留在清單上重試；成功的已在背景排隊，到任務頁看得到
      toast(`${added} 個已加入佇列，${failed} 個失敗`, viewTasks);
    } else if (items.length === 1) {
      // 單一項目直接看它的進度
      setGoTo(`/jobs/${items[0].jobId}`);
      toast("已加入佇列", viewTasks);
    } else {
      // 多個項目從清單拿掉，進度在任務頁
      up.clearDone();
      toast(`${added} 個已加入佇列`, viewTasks);
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
    if (files.length) toast(`已加入 ${files.length} 個檔案，按「開始轉錄」上傳`);
  };

  const addVideos = (videos: YoutubePick[], done: boolean) => {
    const n = up.addVideos(videos);
    toast(n ? `已加入 ${n} 支影片，按「開始轉錄」送出` : "清單裡已經有這些影片");
    // 手機：全部處理完就關掉面板，回到清單看得到剛加入的影片（有錯誤或還有播放清單要勾選時留著）
    if (isMobile && done) setSheet(null);
  };

  const finished = jobs?.filter((j) => !isActive(j)) ?? [];
  const pendingCount = up.items.filter((i) => i.state === "pending").length;

  const urlInput = <YoutubeInput disabled={up.uploading} onAdd={addVideos} />;
  const optionsForm = <JobOptionsForm value={opts} onChange={setOpts} disabled={up.uploading} />;

  return (
    <main className={`page home${isMobile ? " with-dock" : ""}`}>
      <h1>錄音</h1>

      <section aria-label="新增錄音" className="add-sec">
        {!isMobile && (
          <>
            <FileDropzone
              multiple
              disabled={up.uploading}
              onPickFiles={addFiles}
              title="把錄音拖到這裡"
              hint="可以一次放多個檔案：.qta、.m4a、.mp3、.wav、.mp4 與其他影音檔。"
            />
            <div className="add-panel">
              {urlInput}
              {optionsForm}
            </div>
          </>
        )}
        {up.notice && <p className="help">{up.notice}</p>}
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
        !error && <p className="empty">載入中…</p>
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
              選擇錄音檔
            </button>
            <button type="button" className="btn" onClick={() => setSheet("url")}>
              貼上網址
            </button>
            <button type="button" className="btn" onClick={() => setSheet("set")}>
              設定
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
          <BottomSheet open={sheet === "url"} title="貼上網址" initialFocus="#yt-urls" onClose={() => setSheet(null)}>
            <div className="add-panel">{urlInput}</div>
          </BottomSheet>
          <BottomSheet open={sheet === "set"} title="轉錄設定" initialFocus=".opts select" onClose={() => setSheet(null)}>
            <div className="add-panel">{optionsForm}</div>
          </BottomSheet>
        </>
      )}

      {blocker.state === "blocked" && (
        <ConfirmModal
          title="上傳還沒完成"
          message="離開這個頁面會停止上傳。已經上傳完成的檔案會繼續在背景轉錄；還沒上傳的檔案要重新選擇。"
          confirmLabel="停止上傳並離開"
          cancelLabel="繼續上傳"
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

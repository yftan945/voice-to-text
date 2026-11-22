import { useLayoutEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import Icon from "../../components/Icon";
import { OptionsDialog } from "../../components/JobDialogs";
import { ConfirmModal } from "../../components/Modal";
import StepBar from "../../components/StepBar";
import { useToast } from "../../components/Toast";
import { approxDuration, clockAfter, duration, etaText, languageName, timeOfDay } from "../../format";
import { modelLabel, useFinePointer } from "../../hooks";
import { isActive } from "../../jobs";
import type { Job, ModelList, MoveTarget } from "../../types";

/** 處理中在前，接著依排隊順位。 */
function byQueue(a: Job, b: Job): number {
  if (a.status !== b.status) return a.status === "running" ? -1 : 1;
  const ra = a.queue_rank ?? Number.MAX_SAFE_INTEGER;
  const rb = b.queue_rank ?? Number.MAX_SAFE_INTEGER;
  return ra - rb || a.created_at - b.created_at;
}

interface Batch {
  id: string;
  label: string;
  total: number;
  done: number;
  failed: number;
  canceled: number;
  active: Job[];
  /** 整批預估幾秒後全部完成；有任務無法估算時為 null */
  finish: number | null;
}

/** 有任務還在排隊或處理中、而且不只一個檔案的批次。 */
function activeBatches(jobs: Job[]): Batch[] {
  const groups = new Map<string, Job[]>();
  for (const j of jobs) {
    if (!j.batch_id) continue;
    const g = groups.get(j.batch_id);
    if (g) g.push(j);
    else groups.set(j.batch_id, [j]);
  }
  const out: Batch[] = [];
  for (const [id, list] of groups) {
    const active = list.filter(isActive);
    if (active.length === 0 || list.length < 2) continue;
    const finishes = active.map((j) => j.eta_finish_seconds);
    out.push({
      id,
      label: `${timeOfDay(Math.min(...list.map((j) => j.created_at)))} 上傳`,
      total: list.length,
      done: list.filter((j) => j.status === "done").length,
      failed: list.filter((j) => j.status === "failed").length,
      canceled: list.filter((j) => j.status === "canceled").length,
      active,
      finish: finishes.some((f) => f == null) ? null : Math.max(...(finishes as number[])),
    });
  }
  return out.sort((a, b) => a.label.localeCompare(b.label));
}

const textSource = (job: Job, models: ModelList | null) =>
  // 有人工字幕而且要用字幕的 YouTube 任務不跑辨識模型
  job.source === "youtube" && job.use_captions && job.caption_lang
    ? `YouTube 人工字幕（${languageName(job.caption_lang)}）`
    : modelLabel(models, job.model);

const speakerText = (job: Job) =>
  !job.diarize ? "不分講者" : job.num_speakers ? `${job.num_speakers} 位講者` : "自動判斷講者人數";

/** 字典名稱；字典清單還沒載入、或名稱都對不上（例如剛新增、清單還沒重新讀）時只寫本數。 */
function dictText(job: Job, dictNames: Map<number, string> | null): string {
  const ids = job.dictionary_ids;
  if (!ids.length) return "不用字典";
  const names = dictNames ? ids.map((id) => dictNames.get(id)).filter(Boolean) : [];
  return names.length ? `字典：${names.join("、")}` : `字典 ${ids.length} 本`;
}

/**
 * 任務頁「處理中與排隊中」：台本式的列（左欄是狀態或順位，右欄是檔名、步驟、預估）。
 * 排隊中的任務可以拖曳（桌機）、移到最前、上移／下移（手機與鍵盤）、修改設定；單筆取消與整批取消。
 * 每筆列出目前的設定（模型、講者、字典），改了設定看得出差別。
 * 即時更新靠整個 App 共用的 SSE（jobs.tsx 的 JobsProvider，/api/events），這裡不另外輪詢。
 */
export default function QueueSection({
  jobs,
  models,
  dictNames,
  onUpdate,
  onOptionsSaved,
}: {
  jobs: Job[];
  models: ModelList | null;
  /** 字典 id → 名稱（設定摘要用）；還沒載入為 null */
  dictNames: Map<number, string> | null;
  /** 修改設定成功後（重新讀字典清單，摘要才對得上新選的字典） */
  onOptionsSaved?: () => void;
  /** 把 API 回傳的最新任務併回列表 */
  onUpdate: (jobs: Job[]) => void;
}) {
  const active = useMemo(() => jobs.filter(isActive).sort(byQueue), [jobs]);
  const batches = useMemo(() => activeBatches(jobs), [jobs]);
  const batchLabels = useMemo(() => new Map(batches.map((b) => [b.id, b.label])), [batches]);
  const [error, setError] = useState("");
  const [dragId, setDragId] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ id: string; after: boolean } | null>(null);
  const [confirmBatch, setConfirmBatch] = useState<Batch | null>(null);
  const [confirmRunning, setConfirmRunning] = useState<Job | null>(null);
  const [editing, setEditing] = useState<Job | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const movingRef = useRef(false);
  // 按鈕移動後列表重新排列，焦點要回到同一個任務的同一顆按鈕（鍵盤操作才不會迷路）
  const refocus = useRef<{ id: string; action: string } | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const fine = useFinePointer();
  const toast = useToast();

  useLayoutEffect(() => {
    const want = refocus.current;
    const list = listRef.current;
    if (!want || !list) return;
    const row = list.querySelector<HTMLElement>(`[data-job="${want.id}"]`);
    if (!row) return;
    refocus.current = null;
    // 原本那顆按鈕停用了（例如移到最後一位後的「下移」）就改用反方向的按鈕
    const fallback: Record<string, string> = { front: "down", up: "down", down: "up" };
    const btn =
      row.querySelector<HTMLButtonElement>(`[data-action="${want.action}"]:not(:disabled)`) ??
      row.querySelector<HTMLButtonElement>(`[data-action="${fallback[want.action] ?? ""}"]:not(:disabled)`) ??
      row.querySelector<HTMLButtonElement>("button:not(:disabled)");
    // 這一列已經沒有可以按的按鈕：焦點放到區塊標題，不要掉到頁面最上面
    (btn ?? headingRef.current)?.focus();
  });

  const queuedCount = active.filter((j) => j.status === "queued").length;
  const runningCount = active.length - queuedCount;
  const last = active[active.length - 1];
  const allFinish = !last || active.some((j) => j.eta_finish_seconds == null) ? null : last.eta_finish_seconds;
  const fromHistory = active.some((j) => j.estimate_basis === "history");

  const move = async (job: Job, target: MoveTarget, action: string | null) => {
    if (movingRef.current) return;
    movingRef.current = true;
    setError("");
    try {
      const r = await api.moveJob(job.id, target);
      if (action) refocus.current = { id: job.id, action };
      onUpdate(r.jobs);
      setFlash(job.id);
      const moved = r.jobs.find((j) => j.id === job.id);
      if (moved?.queue_rank) toast(`「${job.filename}」現在是第 ${moved.queue_rank} 位`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      movingRef.current = false;
    }
  };

  const cancel = async (job: Job) => {
    setError("");
    try {
      onUpdate([await api.cancel(job.id)]);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onDragOver = (e: DragEvent<HTMLLIElement>, job: Job) => {
    if (!dragId || job.status !== "queued") return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (job.id === dragId) {
      if (drop) setDrop(null);
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const after = e.clientY > rect.top + rect.height / 2;
    if (drop?.id !== job.id || drop.after !== after) setDrop({ id: job.id, after });
  };

  const onDrop = (e: DragEvent<HTMLLIElement>, job: Job) => {
    e.preventDefault();
    const src = active.find((j) => j.id === dragId);
    const target = drop;
    setDragId(null);
    setDrop(null);
    if (!src || !target || target.id === src.id || job.status !== "queued") return;
    void move(src, target.after ? { after: target.id } : { before: target.id }, null);
  };

  return (
    <section className="sec" aria-labelledby="queue-title">
      <div className="sec-head">
        <h2 id="queue-title" ref={headingRef} tabIndex={-1}>
          處理中與排隊中
        </h2>
        {/* 都是 0 時下面的空狀態已經說了，不重複顯示「處理中 0　排隊中 0」 */}
        {active.length > 0 && (
          <span className="sub num">
            處理中 {runningCount}　排隊中 {queuedCount}
          </span>
        )}
        {allFinish != null && (
          <span className="sub num">
            {allFinish < 60 ? "全部即將完成" : `全部約 ${clockAfter(allFinish)} 完成（${approxDuration(allFinish)}後）`}
          </span>
        )}
      </div>

      {active.length === 0 ? (
        <p className="empty">
          目前沒有處理中或排隊中的錄音。<Link to="/" className="nowrap">
            前往錄音頁上傳
          </Link>
        </p>
      ) : (
        <>
          <p className="help queue-basis">
            時間是{fromHistory ? "依這台機器過去的實際處理速度" : "依預設速度"}估算的大概值
            {fromHistory ? "" : "，完成幾個任務後會越來越準"}。
            {fine && queuedCount > 1 && "可以拖曳排隊中的錄音調整順序。"}
          </p>

          {batches.map((b) => (
            <div key={b.id} className="batch">
              <span className="batch-name">批次　{b.label}</span>
              <span className="num">
                已完成 {b.done} 個，共 {b.total} 個
                {b.failed > 0 && `，失敗 ${b.failed} 個`}
                {b.canceled > 0 && `，已取消 ${b.canceled} 個`}
              </span>
              <span className="muted num">
                {b.finish == null
                  ? "無法估算完成時間"
                  : b.finish < 60
                    ? "即將全部完成"
                    : `約 ${clockAfter(b.finish)} 全部完成`}
              </span>
              <span className="grow" />
              <button type="button" className="tbtn danger" onClick={() => setConfirmBatch(b)}>
                整批取消
              </button>
            </div>
          ))}

          {error && <p className="err" role="alert">{error}</p>}

          <ol className="queue" ref={listRef}>
            {active.map((job) => {
              const queued = job.status === "queued";
              const rank = job.queue_rank;
              const pct = Math.round(job.progress * 100);
              const dropClass = drop?.id === job.id ? (drop.after ? " over-bot" : " over-top") : "";
              const batch = job.batch_id ? batchLabels.get(job.batch_id) : undefined;
              const draggable = fine && queued && queuedCount > 1;
              return (
                <li
                  key={job.id}
                  data-job={job.id}
                  className={`job${dragId === job.id ? " dragging" : ""}${dropClass}${flash === job.id ? " flash" : ""}`}
                  draggable={draggable}
                  onAnimationEnd={() => setFlash(null)}
                  onDragStart={(e) => {
                    e.dataTransfer.effectAllowed = "move";
                    e.dataTransfer.setData("text/plain", job.id);
                    setDragId(job.id);
                  }}
                  onDragEnd={() => {
                    setDragId(null);
                    setDrop(null);
                  }}
                  onDragOver={(e) => onDragOver(e, job)}
                  onDrop={(e) => onDrop(e, job)}
                >
                  <div className={`cue ${queued ? "wait" : "run"}`}>
                    {draggable && (
                      <span className="grip" aria-hidden="true" title="拖曳調整順序">
                        <Icon name="grip" size={16} />
                      </span>
                    )}
                    {queued ? (
                      <span>
                        第 <span className="num">{rank ?? "—"}</span> 位
                      </span>
                    ) : job.cancel_requested ? (
                      "取消中"
                    ) : (
                      "處理中"
                    )}
                  </div>
                  <div className="body">
                    <div className="nm break">
                      {job.source === "youtube" && <span className="src">YouTube</span>}
                      <Link to={`/jobs/${job.id}`} draggable={false}>
                        {job.filename}
                      </Link>
                    </div>
                    {!queued && <StepBar job={job} />}
                    <div className="info num">
                      {!queued && (
                        <span>
                          <b>
                            {job.stage} {pct}%
                          </b>
                        </span>
                      )}
                      <span>長度 {duration(job.duration)}</span>
                      <span>{textSource(job, models)}</span>
                      <span>{speakerText(job)}</span>
                      <span className="break">{dictText(job, dictNames)}</span>
                      {job.estimate_seconds != null && <span>處理時間{approxDuration(job.estimate_seconds)}</span>}
                      <span>{etaText(job)}</span>
                      {batch && <span>批次 {batch}</span>}
                    </div>
                  </div>
                  <div className="acts">
                    {queued && (
                      <>
                        <button
                          type="button"
                          className="tbtn"
                          data-action="front"
                          disabled={rank === 1}
                          onClick={() => move(job, { to: "front" }, "front")}
                          aria-label={`移到最前：${job.filename}`}
                        >
                          移到最前
                        </button>
                        <button
                          type="button"
                          className="tbtn"
                          data-action="up"
                          disabled={rank === 1}
                          onClick={() => move(job, { to: "up" }, "up")}
                          aria-label={`上移：${job.filename}`}
                        >
                          上移
                        </button>
                        <button
                          type="button"
                          className="tbtn"
                          data-action="down"
                          disabled={rank === queuedCount}
                          onClick={() => move(job, { to: "down" }, "down")}
                          aria-label={`下移：${job.filename}`}
                        >
                          下移
                        </button>
                        <button
                          type="button"
                          className="tbtn"
                          data-action="options"
                          onClick={() => setEditing(job)}
                          aria-label={`修改設定：${job.filename}`}
                        >
                          設定
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      className="tbtn danger"
                      data-action="cancel"
                      onClick={() => (queued ? cancel(job) : setConfirmRunning(job))}
                      disabled={job.cancel_requested}
                      aria-label={`取消：${job.filename}`}
                    >
                      {job.cancel_requested ? "取消中…" : "取消"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ol>
        </>
      )}

      {confirmBatch && (
        <ConfirmModal
          title="整批取消"
          message={
            <>
              <p>
                取消「{confirmBatch.label}」這批還沒完成的 {confirmBatch.active.length} 個任務？
              </p>
              <p>
                排隊中的會直接取消；處理中的會在目前步驟結束後停止。
                {confirmBatch.done > 0 && `已完成的 ${confirmBatch.done} 個不受影響。`}
              </p>
            </>
          }
          confirmLabel="整批取消"
          cancelLabel="返回"
          danger
          onClose={() => setConfirmBatch(null)}
          onConfirm={async () => {
            const r = await api.cancelBatch(confirmBatch.id);
            onUpdate(r.jobs);
            setConfirmBatch(null);
          }}
        />
      )}
      {editing && (
        <OptionsDialog
          job={editing}
          onClose={() => setEditing(null)}
          onDone={(j) => {
            setEditing(null);
            onUpdate([j]);
            onOptionsSaved?.();
          }}
          onConflict={async () => {
            // 409：任務已經開始處理或結束。先讀最新狀態併回列表（不等 SSE），
            // 還在處理中就把焦點放在這一列的「取消」，已經結束（列不見了）就放在區塊標題
            const id = editing.id;
            setEditing(null);
            let fresh: Job | null = null;
            try {
              fresh = await api.job(id);
            } catch {
              /* 讀不到（可能已刪除）：SSE 之後會更新列表 */
            }
            if (fresh && isActive(fresh)) refocus.current = { id, action: "cancel" };
            else headingRef.current?.focus();
            if (fresh) onUpdate([fresh]);
          }}
        />
      )}
      {confirmRunning && (
        <ConfirmModal
          title="取消處理中的錄音"
          message={`「${confirmRunning.filename}」正在${confirmRunning.stage.replace(/中$/, "")}，取消後這次的進度會丟棄，會在目前步驟結束後停止。`}
          confirmLabel="取消處理"
          cancelLabel="繼續處理"
          danger
          onClose={() => setConfirmRunning(null)}
          onConfirm={async () => {
            onUpdate([await api.cancel(confirmRunning.id)]);
            setConfirmRunning(null);
          }}
        />
      )}
    </section>
  );
}

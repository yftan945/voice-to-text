import { useState } from "react";
import { Link } from "react-router-dom";
import { DeleteDialog, RerunDialog } from "../../components/JobDialogs";
import { MiniBand } from "../../components/SpeakerBand";
import { dateTime, duration } from "../../format";
import { modelLabel } from "../../hooks";
import { colorClass, displaySpeaker, mergeRuns } from "../../lib/speakers";
import type { Job, ModelList, SpeakerSummary } from "../../types";

/** 迷你色帶＋講者占比圖例（資料來自列表 API 的 speaker_summary，不另外載入逐字稿）。 */
function SpeakerMini({ summary }: { summary: SpeakerSummary }) {
  // 舊的摘要存的名字可能還是 SPEAKER_xx：一律換成「講者 N」，和結果頁一致
  const speaking = summary.speakers
    .map((s, i) => ({ ...s, name: displaySpeaker(s.label, s.name), i }))
    .filter((s) => s.share > 0)
    .sort((a, b) => b.share - a.share);
  if (!summary.runs.length || !speaking.length) return null;
  // 首頁的色帶很窄：最短段落至少 1.4% 寬，5 位講者的會議也讀得出主要發言者
  const minLen = summary.duration * 0.014;
  const runs = mergeRuns(
    summary.runs.map(([s, e, sp]) => ({ s, e: Math.max(e, Math.min(summary.duration, s + minLen)), sp })),
    summary.duration * 0.004,
  );
  const pct = (share: number) => (share < 0.005 ? "不到 1%" : `${Math.round(share * 100)}%`);
  const label = `${speaking.length} 位講者：` + speaking.map((s) => `${s.name} ${pct(s.share)}`).join("、");
  return (
    <>
      <MiniBand runs={runs} duration={summary.duration} label={label} />
      {speaking.length > 1 && (
        <div className="legend" aria-hidden="true">
          {speaking.map((s) => (
            <span key={s.label} className={colorClass(s.i)}>
              <i className="sw" />
              {s.name} <span className="num">{pct(s.share)}</span>
            </span>
          ))}
        </div>
      )}
    </>
  );
}

const textSource = (job: Job, models: ModelList | null) =>
  job.caption_source?.startsWith("manual:") ? "YouTube 人工字幕" : modelLabel(models, job.model);

/** 首頁「已完成」：已結束的錄音（完成、失敗、已取消），每筆有迷你講者色帶；可重新轉錄、刪除。 */
export default function DoneSection({
  jobs,
  models,
  onReplace,
  onRemove,
}: {
  jobs: Job[];
  models: ModelList | null;
  onReplace: (job: Job) => void;
  onRemove: (id: string) => void;
}) {
  const [deleting, setDeleting] = useState<Job | null>(null);
  const [rerunning, setRerunning] = useState<Job | null>(null);

  return (
    <section className="sec" aria-labelledby="done-title">
      <div className="sec-head">
        <h2 id="done-title">已完成</h2>
        <span className="sub num">{jobs.length} 筆</span>
      </div>
      {jobs.length === 0 ? (
        <p className="empty">還沒有完成的錄音。</p>
      ) : (
        <ul className="done-list">
          {jobs.map((job) => {
            const n = job.speaker_summary?.speakers.filter((s) => s.share > 0).length ?? 0;
            return (
              <li key={job.id} className={`done-row${job.status !== "done" ? " fail" : ""}`}>
                <div className="main">
                  <div className="nm break">
                    {job.source === "youtube" && <span className="src">YouTube</span>}
                    {/* 真正的連結：::after 撐滿整列，鍵盤 Tab／Enter 可操作 */}
                    <Link to={`/jobs/${job.id}`} className="row-link">
                      {job.filename}
                    </Link>
                  </div>
                  {job.status === "done" && job.has_transcript && job.speaker_summary && (
                    <SpeakerMini summary={job.speaker_summary} />
                  )}
                  {job.status === "failed" && <p className="why">失敗：{job.error}</p>}
                  {job.status === "canceled" && <p className="why muted">已取消</p>}
                  {(!job.has_audio || (job.status === "done" && !job.has_transcript)) && (
                    <p className="flags">
                      {!job.has_audio && <span>音檔已刪除</span>}
                      {job.status === "done" && !job.has_transcript && <span>逐字稿已刪除</span>}
                    </p>
                  )}
                </div>
                <div className="side">
                  <b className="num">{dateTime(job.created_at)}</b>
                  <span className="num">{duration(job.duration)}</span>
                  <span>{n > 0 ? `${n} 位講者` : textSource(job, models)}</span>
                  <span className="side-acts">
                    <button type="button" className="tbtn" disabled={!job.has_audio} onClick={() => setRerunning(job)}>
                      重新轉錄
                    </button>
                    <button type="button" className="tbtn danger" onClick={() => setDeleting(job)}>
                      刪除
                    </button>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {deleting && (
        <DeleteDialog
          job={deleting}
          onClose={() => setDeleting(null)}
          onDone={(_, j) => {
            setDeleting(null);
            if (j) onReplace(j);
            else onRemove(deleting.id);
          }}
        />
      )}
      {rerunning && (
        <RerunDialog
          job={rerunning}
          onClose={() => setRerunning(null)}
          onDone={(j) => {
            setRerunning(null);
            onReplace(j);
          }}
        />
      )}
    </section>
  );
}

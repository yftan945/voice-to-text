import { useEffect, useState } from "react";
import { api } from "../api";
import { duration, languageName } from "../format";
import type { YoutubePlaylist, YoutubeVideo } from "../types";
import Modal from "./Modal";

/** 加進上傳清單的一支影片。captionLang：undefined＝還沒查（處理時自動判斷）、null＝沒有人工字幕。 */
export interface YoutubePick {
  videoId: string;
  title: string;
  channel: string;
  duration: number | null;
  captionLang: string | null | undefined;
}

export const pickFromVideo = (v: YoutubeVideo): YoutubePick => ({
  videoId: v.video_id,
  title: v.title,
  channel: v.channel,
  duration: v.duration,
  captionLang: v.caption_lang,
});

/** 字幕狀態的說明文字（上傳清單與播放清單共用）。 */
export function captionText(lang: string | null | undefined): string {
  if (lang === undefined) return "字幕：處理時自動判斷";
  if (lang === null) return "沒有人工字幕，用模型轉錄";
  return `有人工字幕（${languageName(lang)}），直接使用字幕`;
}

const DETAIL_CONCURRENCY = 3;

/**
 * 「貼上 YouTube 網址」：一行一個，可以是影片或播放清單。
 * 單一影片直接加入；播放清單先列出所有影片（逐支補上有沒有人工字幕）讓使用者勾選再加入。
 */
export default function YoutubeInput({
  disabled,
  onAdd,
}: {
  disabled: boolean;
  /** done：這次貼的網址都處理完了（沒有錯誤、沒有還要勾選的播放清單），手機可以關掉面板 */
  onAdd: (videos: YoutubePick[], done: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<{ input: string; error: string }[]>([]);
  const [playlists, setPlaylists] = useState<YoutubePlaylist[]>([]);

  const resolve = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setErrors([]);
    try {
      const r = await api.resolveYoutube(text);
      const errs: { input: string; error: string }[] = [];
      const videos: YoutubePick[] = [];
      const lists: YoutubePlaylist[] = [];
      for (const item of r.items) {
        if (item.kind === "invalid") errs.push({ input: item.input, error: item.error });
        else if (item.kind === "video") {
          if (item.video && !item.video.error) videos.push(pickFromVideo(item.video));
          else errs.push({ input: item.input, error: item.error ?? item.video?.error ?? "無法處理這部影片" });
        } else if (item.playlist) {
          if (item.playlist.entries.length === 0) errs.push({ input: item.input, error: "播放清單裡沒有影片" });
          else lists.push(item.playlist);
        } else errs.push({ input: item.input, error: item.error ?? "無法讀取播放清單" });
      }
      if (r.truncated) errs.push({ input: "（超過 50 行）", error: "一次最多 50 個網址，後面的沒有處理" });
      if (videos.length) onAdd(videos, errs.length === 0 && lists.length === 0);
      setErrors(errs);
      setPlaylists(lists);
      // 全部處理掉的話清空輸入框；有錯的留下來方便修改
      setText(errs.filter((e) => !e.input.startsWith("（")).map((e) => e.input).join("\n"));
    } catch (e) {
      setErrors([{ input: "", error: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="url-form" aria-label="YouTube 網址">
      <label className="lab" htmlFor="yt-urls">
        貼上 YouTube 網址<span className="d">一行一個，可以是影片、Shorts、直播回放或播放清單</span>
      </label>
      <div className="url-row">
        <textarea
          id="yt-urls"
          className="field"
          rows={Math.min(5, Math.max(1, text.split("\n").length))}
          value={text}
          disabled={disabled || busy}
          placeholder="https://www.youtube.com/watch?v=…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void resolve();
          }}
        />
        <button type="button" className="btn" disabled={disabled || busy || !text.trim()} onClick={resolve}>
          {busy ? "讀取影片資訊中…" : "加入影片"}
        </button>
      </div>
      {errors.length > 0 ? (
        <ul className="yt-errors" role="alert">
          {errors.map((e, i) => (
            <li key={i}>
              {e.input && <span className="break muted">{e.input}：</span>}
              <span className="error">{e.error}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">有人工字幕就直接用字幕，沒有就用語音辨識。<span className="d-only">按 Ctrl＋Enter 也可以加入。</span></p>
      )}
      {playlists.length > 0 && (
        <PlaylistPicker
          key={playlists[0].playlist_id}
          playlist={playlists[0]}
          remaining={playlists.length - 1}
          onClose={() => setPlaylists((p) => p.slice(1))}
          onAdd={(videos) => {
            onAdd(videos, playlists.length === 1 && errors.length === 0);
            setPlaylists((p) => p.slice(1));
          }}
        />
      )}
    </section>
  );
}

type Detail = { state: "loading" } | { state: "ok"; video: YoutubeVideo } | { state: "error"; error: string };

function PlaylistPicker({
  playlist,
  remaining,
  onClose,
  onAdd,
}: {
  playlist: YoutubePlaylist;
  remaining: number;
  onClose: () => void;
  onAdd: (videos: YoutubePick[]) => void;
}) {
  const selectable = playlist.entries.filter((e) => !e.unavailable);
  const [checked, setChecked] = useState<Set<string>>(() => new Set(selectable.map((e) => e.video_id)));
  const [details, setDetails] = useState<Record<string, Detail>>({});

  // 逐支查有沒有人工字幕（同時最多 3 支）；查到無法處理的影片自動取消勾選
  useEffect(() => {
    let alive = true;
    const queue = selectable.map((e) => e.video_id);
    const worker = async () => {
      for (;;) {
        const id = queue.shift();
        if (!id || !alive) return;
        setDetails((d) => ({ ...d, [id]: { state: "loading" } }));
        try {
          const v = await api.youtubeVideo(id);
          if (!alive) return;
          setDetails((d) => ({ ...d, [id]: v.error ? { state: "error", error: v.error } : { state: "ok", video: v } }));
          if (v.error) setChecked((c) => new Set([...c].filter((x) => x !== id)));
        } catch (e) {
          if (!alive) return;
          setDetails((d) => ({ ...d, [id]: { state: "error", error: (e as Error).message } }));
          setChecked((c) => new Set([...c].filter((x) => x !== id)));
        }
      }
    };
    for (let i = 0; i < DETAIL_CONCURRENCY; i++) void worker();
    return () => {
      alive = false;
    };
  }, [playlist.playlist_id]);

  const toggle = (id: string) =>
    setChecked((c) => {
      const n = new Set(c);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const usable = selectable.filter((e) => details[e.video_id]?.state !== "error");
  const allOn = usable.length > 0 && usable.every((e) => checked.has(e.video_id));
  const pending = selectable.filter((e) => !details[e.video_id] || details[e.video_id].state === "loading").length;
  const count = checked.size;

  const submit = () => {
    const picks = playlist.entries
      .filter((e) => checked.has(e.video_id))
      .map((e): YoutubePick => {
        const d = details[e.video_id];
        return d?.state === "ok"
          ? pickFromVideo(d.video)
          : { videoId: e.video_id, title: e.title, channel: e.channel, duration: e.duration, captionLang: undefined };
      });
    onAdd(picks);
  };

  return (
    <Modal
      title="選擇要轉錄的影片"
      wide
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {remaining ? "略過這個清單" : "取消"}
          </button>
          <button type="button" className="btn primary" disabled={count === 0} onClick={submit}>
            加入 {count} 支影片
          </button>
        </>
      }
    >
      <p className="break">
        <strong className="pl-title">{playlist.title}</strong>
        <span className="muted">
          {playlist.channel ? `，${playlist.channel}` : ""}，共 {playlist.count} 支
        </span>
      </p>
      {playlist.truncated && <p className="help">清單太長，只列出前 {playlist.entries.length} 支。</p>}
      <div className="pl-head">
        <label className="pl-all">
          <input
            type="checkbox"
            checked={allOn}
            onChange={() => setChecked(allOn ? new Set() : new Set(usable.map((e) => e.video_id)))}
          />
          全選
        </label>
        <span className="help" aria-live="polite">
          {pending > 0 ? `檢查字幕中…（還有 ${pending} 支）` : "字幕檢查完成"}
        </span>
      </div>
      <ul className="pl-list">
        {playlist.entries.map((e) => {
          const d = details[e.video_id];
          const err = e.unavailable ? (e.reason ?? "無法處理") : d?.state === "error" ? d.error : null;
          return (
            <li key={e.video_id} className={err ? "disabled" : ""}>
              <label>
                <input
                  type="checkbox"
                  checked={checked.has(e.video_id)}
                  disabled={!!err}
                  onChange={() => toggle(e.video_id)}
                />
                <span className="pl-main">
                  <span className="break">{e.title}</span>
                  <small className="muted">
                    <span className="num">{duration(e.duration)}</span>，
                    {err ? (
                      <span className="error">{err}</span>
                    ) : d?.state === "ok" ? (
                      captionText(d.video.caption_lang)
                    ) : (
                      "檢查字幕中…"
                    )}
                  </small>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </Modal>
  );
}

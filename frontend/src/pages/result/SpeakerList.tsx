import { useEffect, useState } from "react";
import { MenuButton } from "../../components/Menu";
import { duration } from "../../format";
import { avatarChar, colorClass, defaultSpeakerName, speakerName, type SpeakerStat } from "../../lib/speakers";
import type { EditOp, Transcript } from "../../types";

/**
 * 講者清單：頭像、名字、段數與占比、聲紋狀態；改名（原地）、合併到…（選單→確認）、存成或更新聲紋。
 * 桌機在右側 sticky 側欄，手機在上拉播放面板裡。
 */
export default function SpeakerList({
  t,
  labels,
  stats,
  voiceprintLabels,
  readOnly,
  onEdit,
  onRequestMerge,
  onRequestVoiceprint,
}: {
  t: Transcript;
  labels: string[];
  stats: Record<string, SpeakerStat>;
  voiceprintLabels: string[];
  readOnly: boolean;
  onEdit: (op: EditOp) => Promise<boolean>;
  onRequestMerge: (source: string, target: string) => void;
  onRequestVoiceprint: (label: string) => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (readOnly) setRenaming(null);
  }, [readOnly]);

  if (labels.length === 0) return <p className="help">這份逐字稿沒有區分講者。</p>;

  return (
    <ul className="spk-list">
      {labels.map((l, i) => {
        const info = t.speakers[l];
        const st = stats[l] ?? { count: 0, seconds: 0, pct: 0 };
        const display = speakerName(t, l);
        const canVoiceprint = voiceprintLabels.includes(l);
        return (
          <li key={l} className={`spk ${colorClass(i)}`}>
            {renaming === l ? (
              <form
                className="spk-rename"
                onSubmit={async (e) => {
                  e.preventDefault();
                  setBusy(true);
                  const ok = await onEdit({ op: "rename_speaker", speaker: l, name });
                  setBusy(false);
                  if (ok) setRenaming(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.preventDefault();
                    e.stopPropagation();
                    setRenaming(null);
                  }
                }}
              >
                <label className="lab" htmlFor={`rename-${l}`}>
                  {display} 的名字
                </label>
                <div className="spk-rename-row">
                  <input
                    id={`rename-${l}`}
                    className="field"
                    value={name}
                    autoFocus
                    placeholder={defaultSpeakerName(l)}
                    onChange={(e) => setName(e.target.value)}
                  />
                  <button type="submit" className="btn primary" disabled={busy}>
                    儲存
                  </button>
                  <button type="button" className="btn" onClick={() => setRenaming(null)}>
                    取消
                  </button>
                </div>
              </form>
            ) : (
              <>
                <div className="spk-top">
                  <span className="av" aria-hidden="true">
                    {avatarChar(info?.name || l, i)}
                  </span>
                  <span className="spk-name">{display}</span>
                  <span className="pct num">{st.pct}%</span>
                </div>
                {/* 原本的代號（SPEAKER_xx）只出現在這一行；每一項各自不換行，「2 分 53 秒」不會被拆開 */}
                <div className="spk-stat num">
                  {!l.startsWith("MANUAL_") && <span>{l}</span>}
                  <span>{st.count} 段</span>
                  <span>{duration(st.seconds)}</span>
                </div>
                <div className={`spk-vp${info?.voiceprint_id != null ? " ok" : ""}`}>
                  {info?.voiceprint_id != null
                    ? info.similarity != null
                      ? `已比對到聲紋，相似度 ${info.similarity.toFixed(2)}`
                      : "已存成聲紋"
                    : canVoiceprint
                      ? "尚無聲紋"
                      : "手動新增或已合併，沒有聲紋資料"}
                </div>
                {!readOnly && (
                  <div className="spk-acts">
                    <button
                      type="button"
                      className="tbtn"
                      onClick={() => {
                        setName(info?.name ?? "");
                        setRenaming(l);
                      }}
                      aria-label={`改名：${display}`}
                    >
                      改名
                    </button>
                    {labels.length > 1 && (
                      <MenuButton
                        className="tbtn"
                        align="left"
                        aria-label={`把「${display}」合併到…`}
                        items={() => [
                          { header: `把「${display}」合併到` },
                          ...labels
                            .filter((x) => x !== l)
                            .map((x) => ({
                              label: speakerName(t, x),
                              icon: <i className={`sw ${colorClass(labels.indexOf(x))}`} />,
                              onSelect: () => onRequestMerge(l, x),
                            })),
                        ]}
                      >
                        合併到…
                      </MenuButton>
                    )}
                    {canVoiceprint && (
                      <button type="button" className="tbtn" onClick={() => onRequestVoiceprint(l)}>
                        {info?.voiceprint_id != null ? "更新聲紋" : "存成聲紋"}
                      </button>
                    )}
                  </div>
                )}
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

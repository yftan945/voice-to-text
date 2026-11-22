import { useRef, useState, type ReactNode } from "react";
import { fileSize } from "../format";

export const AUDIO_ACCEPT = "audio/*,video/*,.qta,.m4a,.mp3,.wav,.aac,.flac,.ogg,.opus,.webm,.mp4,.mov";

/**
 * 拖放區（四個角落的裁切記號，拖入時轉鈷藍）＋「選擇檔案」按鈕。首頁與聲紋庫共用。
 * 單檔：onPick 收到一個檔案，選好後顯示檔名；multiple：onPickFiles 收到全部選取／拖進來的檔案。
 */
export default function FileDropzone({
  file = null,
  onPick,
  onPickFiles,
  multiple = false,
  disabled = false,
  title,
  hint,
  accept = AUDIO_ACCEPT,
  buttonLabel = "選擇檔案",
  compact = false,
}: {
  file?: File | null;
  onPick?: (f: File) => void;
  onPickFiles?: (files: File[]) => void;
  multiple?: boolean;
  disabled?: boolean;
  title: ReactNode;
  hint: ReactNode;
  accept?: string;
  buttonLabel?: string;
  compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pick = (list: FileList | undefined | null) => {
    const files = list ? Array.from(list) : [];
    if (files.length) {
      if (multiple) onPickFiles?.(files);
      else onPick?.(files[0]);
    }
    // 同一個檔案再選一次也要觸發 onChange
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div
      className={`drop${over ? " over" : ""}${disabled ? " disabled" : ""}${compact ? " compact" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) pick(e.dataTransfer.files);
      }}
    >
      <span className="cm a" />
      <span className="cm b" />
      <span className="cm c" />
      <span className="cm d" />
      <div className="drop-text">
        {file ? (
          <>
            <h2 className="break">{file.name}</h2>
            <p>{fileSize(file.size)}，可以換一個檔案</p>
          </>
        ) : (
          <>
            <h2>{title}</h2>
            <p>{hint}</p>
          </>
        )}
      </div>
      <button type="button" className="btn primary big" disabled={disabled} onClick={() => inputRef.current?.click()}>
        {file ? "換檔案" : buttonLabel}
      </button>
      <input ref={inputRef} type="file" accept={accept} multiple={multiple} hidden onChange={(e) => pick(e.target.files)} />
    </div>
  );
}

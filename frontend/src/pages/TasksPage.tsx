import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useModels } from "../hooks";
import { useJobs } from "../jobs";
import QueueSection from "./tasks/QueueSection";

/** 「任務」：處理中與排隊中的錄音（進度、預估、調整順序、修改設定、取消）。資料來自整個 App 共用的 SSE。 */
export default function TasksPage() {
  const models = useModels();
  const { jobs, error, merge } = useJobs();
  // 設定摘要要顯示字典名稱；讀不到就只顯示本數
  const [dictNames, setDictNames] = useState<Map<number, string> | null>(null);
  const loadDicts = useCallback(() => {
    api.dictionaries().then(
      (list) => setDictNames(new Map(list.map((d) => [d.id, d.name]))),
      () => {
        /* 讀不到就沿用上次的清單（還沒讀過就只顯示本數） */
      },
    );
  }, []);
  useEffect(loadDicts, [loadDicts]);
  return (
    <main className="page tasks">
      <h1>任務</h1>
      <p className="lead">正在處理與排隊等候的錄音。完成後會移到「錄音」頁的已完成清單。</p>
      {error && <p className="err" role="alert">{error}</p>}
      {jobs === null ? (
        !error && <p className="empty">載入中…</p>
      ) : (
        <QueueSection jobs={jobs} models={models} dictNames={dictNames} onUpdate={merge} onOptionsSaved={loadDicts} />
      )}
    </main>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useModels } from "../hooks";
import { useT } from "../i18n";
import { useJobs } from "../jobs";
import QueueSection from "./tasks/QueueSection";

/** 「任務」：處理中與排隊中的錄音（進度、預估、調整順序、修改設定、取消）。資料來自整個 App 共用的 SSE。 */
export default function TasksPage() {
  const t = useT();
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
      <h1>{t("nav.tasks")}</h1>
      <p className="lead">{t("tasks.lead")}</p>
      {error && <p className="err" role="alert">{error}</p>}
      {jobs === null ? (
        !error && <p className="empty">{t("common.loading")}</p>
      ) : (
        <QueueSection jobs={jobs} models={models} dictNames={dictNames} onUpdate={merge} onOptionsSaved={loadDicts} />
      )}
    </main>
  );
}

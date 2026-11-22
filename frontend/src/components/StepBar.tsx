import { jobSteps } from "../lib/steps";
import type { Job } from "../types";

/**
 * 處理步驟：一格一個步驟的細線（完成墨色、目前鈷藍、未到細線）；下面是步驟名稱。
 * 窄的時候名稱放不下一格，只顯示目前步驟的名稱（base.css 的 container query）：
 * 前半段的步驟從它那一格往右延伸、後半段的往左延伸（--col、--align），對齊細線又不會被切掉。
 */
export default function StepBar({ job }: { job: Job }) {
  const { names, current, fraction } = jobSteps(job);
  const pct = Math.round(job.progress * 100);
  return (
    <div className="steps" data-n={names.length} style={{ ["--n" as string]: names.length }}>
      <div
        className="cells"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${job.stage} ${pct}%`}
      >
        {names.map((n, i) => (
          <i
            key={n}
            className={i < current ? "done" : i === current ? `cur${fraction === null ? " indet" : ""}` : ""}
            style={i === current && fraction !== null ? { ["--p" as string]: `${Math.round(fraction * 100)}%` } : undefined}
          />
        ))}
      </div>
      <div className="names" aria-hidden="true">
        {names.map((n, i) => (
          <span
            key={n}
            className={i === current ? "cur" : ""}
            style={
              i === current
                ? i < names.length / 2
                  ? { ["--col" as string]: `${i + 1} / -1`, ["--align" as string]: "left" }
                  : { ["--col" as string]: `1 / ${i + 2}`, ["--align" as string]: "right" }
                : undefined
            }
          >
            {n}
          </span>
        ))}
      </div>
    </div>
  );
}

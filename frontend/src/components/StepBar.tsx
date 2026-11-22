import { jobSteps } from "../lib/steps";
import type { Job } from "../types";

/** 處理步驟：一格一個步驟的細線（完成墨色、目前鈷藍、未到細線）；下面是步驟名稱。 */
export default function StepBar({ job }: { job: Job }) {
  const { names, current, fraction } = jobSteps(job);
  const pct = Math.round(job.progress * 100);
  return (
    <div className="steps" style={{ ["--n" as string]: names.length }}>
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
          <span key={n} className={i === current ? "cur" : ""}>
            {n}
          </span>
        ))}
      </div>
    </div>
  );
}

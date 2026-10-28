import type { Jobs } from "./types";

export function JobsBadge({ jobs }: { jobs: Jobs }) {
  const n = (jobs.running ? 1 : 0) + jobs.queued.length + jobs.free.length;
  return (
    <span className={n ? "jobs busy" : "jobs"} role="status">
      {n ? `${jobs.running?.name ?? jobs.free[0]?.name ?? "working"}${n > 1 ? ` (+${n - 1})` : ""}` : "idle"}
      {jobs.errors.length > 0 && <span className="error"> · {jobs.errors[jobs.errors.length - 1].error}</span>}
    </span>
  );
}

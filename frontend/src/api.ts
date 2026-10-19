export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface JobResult {
  scenes: string[];
  seeds?: (number | null)[];
  images: string[];
  clips: string[];
  video: string | null;
  seed?: number;
}

export interface Job {
  id: string;
  prompt: string;
  settings: Partial<JobSettings> & Record<string, unknown>;
  state: JobState;
  stage: string;
  progress: number;
  error: string | null;
  result: JobResult | null;
  created: number;
  updated: number;
}

export interface Health {
  backend: string;
  comfyui: "ok" | "down";
  devices?: string[];
  error?: string;
}

export interface JobSettings {
  width: number;
  height: number;
  max_scenes: number;
  motion: "none" | "kenburns" | "ltx";
  style: string;
  clip_seconds: number;
}

export const isActive = (job: Job) => job.state === "queued" || job.state === "running";

/** URL for a file under outputs/, cache-busted so regenerated scenes show their new version. */
export const outputUrl = (job: Job, path: string) => `/outputs/${path}?v=${Math.floor(job.updated)}`;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

const post = (url: string, body?: unknown) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const getHealth = () => fetch("/api/health").then((r) => json<Health>(r));
export const listJobs = () => fetch("/api/jobs").then((r) => json<Job[]>(r));
export const createJob = (prompt: string, settings: JobSettings) =>
  post("/api/jobs", { prompt, ...settings }).then((r) => json<Job>(r));
export const cancelJob = (id: string) => post(`/api/jobs/${id}/cancel`).then((r) => json<Job>(r));
export const rerunJob = (id: string, keepSeed = false) =>
  post(`/api/jobs/${id}/rerun`, { keep_seed: keepSeed }).then((r) => json<Job>(r));
export const regenerateScene = (id: string, index: number, prompt?: string) =>
  post(`/api/jobs/${id}/scenes/${index}/regenerate`, { prompt: prompt || null }).then((r) => json<Job>(r));
export const deleteJob = (id: string) => fetch(`/api/jobs/${id}`, { method: "DELETE" }).then((r) => json(r));

/** Stream live updates for one job; returns a function that closes the socket. */
export function watchJob(id: string, onUpdate: (job: Job) => void, onClose: () => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${id}`);
  ws.onmessage = (e) => onUpdate(JSON.parse(e.data));
  ws.onclose = onClose;
  return () => ws.close();
}

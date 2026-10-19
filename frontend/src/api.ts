export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  prompt: string;
  settings: Record<string, unknown>;
  state: JobState;
  stage: string;
  progress: number;
  error: string | null;
  result: { scenes: string[]; images: string[]; clips: string[]; video: string | null; seed?: number } | null;
  created: number;
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

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const getHealth = () => fetch("/api/health").then((r) => json<Health>(r));
export const listJobs = () => fetch("/api/jobs").then((r) => json<Job[]>(r));
export const cancelJob = (id: string) =>
  fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).then((r) => json<Job>(r));

export const createJob = (prompt: string, settings: JobSettings) =>
  fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, ...settings }),
  }).then((r) => json<Job>(r));

/** Stream live updates for one job; returns a function that closes the socket. */
export function watchJob(id: string, onUpdate: (job: Job) => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${id}`);
  ws.onmessage = (e) => onUpdate(JSON.parse(e.data));
  return () => ws.close();
}

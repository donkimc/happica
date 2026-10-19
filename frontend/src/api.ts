export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface JobResult {
  scenes: string[];
  narration?: string[];
  motion?: string[];
  durations?: number[];
  subtitles?: string | null;
  subtitles_vtt?: string | null;
  music?: { source: "auto" | "library"; track: string; mp3?: string; title: string; tags?: string } | null;
  ambience?: { source: "auto" | "library"; track: string; mp3?: string; title: string } | null;
  seeds?: (number | null)[];
  images: string[];
  clips: string[];
  audio?: string[];
  video: string | null;
  seed?: number;
}

export interface Track {
  mp3: string;
  flac: string;
  duration: number;
  seed: number;
  peaks: number[];
}

export interface MusicResult {
  tracks: Track[];
  seed: number;
}

export interface MusicSettings {
  title: string;
  tags: string;
  lyrics: string;
  instrumental: boolean;
  duration: number;
  bpm: number;
  keyscale: string;
  timesignature: string;
  language: string;
  variations: number;
}

export interface ImageSettings {
  prompt: string;
  style: string;
  width: number;
  height: number;
  count: number;
}

export interface VoiceSettings {
  text: string;
  voice: string;
  speed: number;
}

export interface SfxSettings {
  prompt: string;
  duration: number;
  loop: boolean;
  enhance: boolean;
  variations: number;
}

export interface MusicOptions {
  keys: string[];
  timesignatures: string[];
  languages: string[];
}

export interface Job {
  id: string;
  kind: "video" | "music" | "sfx" | "image" | "voice";
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
  music?: "ok" | "down";
  ollama?: "ok" | "down" | "model missing";
  llm_model?: string;
}

export interface Voice {
  id: string;
  name: string;
  language: string;
  gender: "female" | "male";
}

export interface JobSettings {
  width: number;
  height: number;
  max_scenes: number;
  motion: "none" | "kenburns" | "ltx";
  style: string;
  clip_seconds: number;
  scene_mode: "lines" | "llm" | "slides" | "musicvideo";
  song_track?: string;
  slides?: { image: string; text: string }[];
  narrate: boolean;
  voice: string;
  subtitles: "off" | "soft" | "burn";
  music: "off" | "auto" | "library";
  music_track: string;
  music_title: string;
  music_volume: number;
  ambience: "off" | "auto" | "library";
  ambience_track: string;
  ambience_title: string;
  ambience_volume: number;
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
export const listVoices = () => fetch("/api/voices").then((r) => json<Voice[]>(r));
export const getMusicOptions = () => fetch("/api/music/options").then((r) => json<MusicOptions>(r));
export const draftSong = (description: string, instrumental: boolean, duration: number) =>
  post("/api/music/draft", { description, instrumental, duration }).then((r) => json<Partial<MusicSettings>>(r));
export async function uploadImage(file: File): Promise<{ path: string; width: number; height: number }> {
  const form = new FormData();
  form.append("file", file);
  return fetch("/api/uploads", { method: "POST", body: form }).then((r) => json(r));
}
export const createImages = (settings: ImageSettings) => post("/api/images", settings).then((r) => json<Job>(r));
export const createVoiceover = (settings: VoiceSettings) => post("/api/voiceover", settings).then((r) => json<Job>(r));
export const createSfx = (settings: SfxSettings) => post("/api/sfx", settings).then((r) => json<Job>(r));
export const createMusic = (settings: MusicSettings) => post("/api/music", settings).then((r) => json<Job>(r));
export const listJobs = () => fetch("/api/jobs").then((r) => json<Job[]>(r));
export const createJob = (prompt: string, settings: JobSettings) =>
  post("/api/jobs", { prompt, ...settings }).then((r) => json<Job>(r));
export const cancelJob = (id: string) => post(`/api/jobs/${id}/cancel`).then((r) => json<Job>(r));
export const rerunJob = (id: string, keepSeed = false) =>
  post(`/api/jobs/${id}/rerun`, { keep_seed: keepSeed }).then((r) => json<Job>(r));
export const regenerateScene = (id: string, index: number, prompt?: string) =>
  post(`/api/jobs/${id}/scenes/${index}/regenerate`, { prompt: prompt || null }).then((r) => json<Job>(r));
export type SoundFields = Pick<
  JobSettings,
  "music" | "music_track" | "music_title" | "music_volume" | "ambience" | "ambience_track" | "ambience_title" | "ambience_volume"
>;
export const changeSound = (id: string, choice: SoundFields) => post(`/api/jobs/${id}/sound`, choice).then((r) => json<Job>(r));
export const regenerateSound = (id: string, layer: "music" | "ambience") =>
  post(`/api/jobs/${id}/sound/regenerate`, { layer }).then((r) => json<Job>(r));
export const deleteJob = (id: string) => fetch(`/api/jobs/${id}`, { method: "DELETE" }).then((r) => json(r));

/** Stream live updates for one job; returns a function that closes the socket. */
export function watchJob(id: string, onUpdate: (job: Job) => void, onClose: () => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${id}`);
  ws.onmessage = (e) => onUpdate(JSON.parse(e.data));
  ws.onclose = onClose;
  return () => ws.close();
}

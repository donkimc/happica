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
  sfx?: SceneSfx[][]; // per scene: its sound effects
  motion_modes?: ("kenburns" | "ltx" | "none")[];
  images: string[];
  clips: string[];
  audio?: string[];
  video: string | null;
  seed?: number;
}

export interface SfxCue {
  prompt: string;
  at?: number; // seconds after the scene appears
  duration?: number;
  volume?: number;
}

export interface SceneSfx extends Required<SfxCue> {
  seed: number;
  mp3?: string | null;
  flac?: string | null;
}

export interface ScriptScene {
  image_prompt: string;
  motion?: string;
  narration: string;
  sfx: SfxCue[];
  motion_mode?: "kenburns" | "ltx" | "none" | null;
}

export interface SongBlock {
  track: string; // a Music library take (.flac)
  placement: "bed" | "intro_outro" | "sung_story";
  volume?: number;
  lyrics_subtitles?: boolean;
}

export interface CaptionStyle {
  font_size_scale?: number;
  color?: string;
  lyric_color?: string;
  position?: "bottom" | "lower_third";
  box?: boolean;
  font?: "rounded" | "handwritten" | "system";
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
  character?: string; // a character job id
  image_model?: ImageModel;
  texture?: Texture | null;
}

// 인물 질감: smooth = a short AI pass for softer skin; detailed = sharpened with a fine grain; none = as drawn
export type Texture = "smooth" | "natural" | "detailed";

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
  kind: "video" | "music" | "sfx" | "image" | "voice" | "character";
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
  vision?: "ok" | "model missing";
  ollama?: "ok" | "down" | "model missing";
  llm_model?: string;
}

export interface Voice {
  id: string;
  name: string;
  language: string;
  gender: "female" | "male";
}

// turbo = SDXL-Turbo, lightning = SDXL-Lightning (both ~5 s a picture, work with characters);
// flux = FLUX.1-schnell, zimage = Z-Image Turbo (minutes a picture, follow descriptions closely; no characters)
export type ImageModel = "turbo" | "lightning" | "flux" | "zimage";
export const SLOW_MODELS: ImageModel[] = ["flux", "zimage"];

export interface JobSettings {
  width: number;
  height: number;
  max_scenes: number;
  motion: "none" | "kenburns" | "ltx";
  style: string;
  clip_seconds: number;
  scene_mode: "lines" | "llm" | "slides" | "musicvideo" | "script";
  script?: ScriptScene[]; // My script: scenes written outside Happica
  characters?: Record<string, string>; // name → look, spelled out in every picture prompt that names them
  sfx_auto?: boolean; // lines/story: the AI adds a sound effect to every scene
  art_characters?: string[]; // cartoon characters (reference art) drawn with IP-Adapter
  image_model?: ImageModel;
  song?: SongBlock | null; // a sung song: bed, intro/outro or the whole story
  caption_style?: CaptionStyle | null;
  output_height?: number | null; // e.g. 1920: 576×1024 is exported as 1080×1920
  upscale?: "fast" | "ai";
  song_track?: string;
  character?: string; // a character job id
  slides?: { image: string; text: string }[];
  slides_narration?: "mine" | "ai"; // My pictures: who writes the narration
  theme?: string; // My pictures: what the video is about
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
  if (res.status === 401 && !res.url.endsWith("/api/login")) {
    // Session missing or expired: the app shows the login screen
    window.dispatchEvent(new Event("happica:unauthorized"));
  }
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

export interface ApiKey {
  id: string;
  name: string;
  hint: string; // first and last characters, e.g. hap_AbC…xYz9
  created: number;
  last_used: number | null;
}
export const listApiKeys = () => fetch("/api/keys").then((r) => json<ApiKey[]>(r));
export const createApiKey = (name: string) => post("/api/keys", { name }).then((r) => json<ApiKey & { key: string }>(r));
export const revokeApiKey = (id: string) => fetch(`/api/keys/${id}`, { method: "DELETE" }).then((r) => json(r));
export const getSession = () => fetch("/api/session").then((r) => json<{ logged_in: boolean }>(r));
export const login = (password: string) => post("/api/login", { password }).then((r) => json<{ ok: boolean }>(r));
export const logout = () => post("/api/logout").then((r) => json<{ ok: boolean }>(r));
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
export const createCharacter = (c: { name: string; photo: string; kind: string; look: string; consent: boolean; refs?: string[]; style?: string; texture?: Texture | null }) =>
  post("/api/characters", c).then((r) => json<Job>(r));
/** 3면도: a front / side / back sheet of a character (an image job) */
export const createTurnaround = (id: string, texture?: Texture | null) =>
  post(`/api/characters/${id}/turnaround`, { count: 1, texture: texture || null }).then((r) => json<Job>(r));
/** Cartoon characters: use a 3-view sheet's pictures as the reference pictures */
export const useViewsAsRefs = (id: string, job: string) => post(`/api/characters/${id}/refs`, { job }).then((r) => json<Job>(r));
export const createVoiceover = (settings: VoiceSettings) => post("/api/voiceover", settings).then((r) => json<Job>(r));
export const createSfx = (settings: SfxSettings) => post("/api/sfx", settings).then((r) => json<Job>(r));
export const createMusic = (settings: MusicSettings) => post("/api/music", settings).then((r) => json<Job>(r));
export const listJobs = () => fetch("/api/jobs").then((r) => json<Job[]>(r));
export const createJob = (prompt: string, settings: JobSettings) =>
  post("/api/jobs", { prompt, ...settings }).then((r) => json<Job>(r));
export const cancelJob = (id: string) => post(`/api/jobs/${id}/cancel`).then((r) => json<Job>(r));
export const rerunJob = (id: string, keepSeed = false) =>
  post(`/api/jobs/${id}/rerun`, { keep_seed: keepSeed }).then((r) => json<Job>(r));
export const regenerateSceneSfx = (id: string, index: number, cue: number, prompt?: string) =>
  post(`/api/jobs/${id}/scenes/${index}/sfx/${cue}/regenerate`, { prompt: prompt || null }).then((r) => json<Job>(r));
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

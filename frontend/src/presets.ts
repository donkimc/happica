import { JobSettings } from "./api";

export interface Preset {
  id: string;
  name: string;
  hint: string;
  settings: Partial<JobSettings>;
}

export const PRESETS: Preset[] = [
  {
    id: "preview",
    name: "Quick preview",
    hint: "Square, short scenes, no narration — check an idea fast",
    settings: { scene_mode: "lines", width: 512, height: 512, max_scenes: 3, motion: "kenburns", clip_seconds: 2, narrate: false },
  },
  {
    id: "story",
    name: "Narrated story",
    hint: "AI storyboard, voiceover and burned-in subtitles",
    settings: { scene_mode: "llm", width: 768, height: 512, max_scenes: 4, motion: "kenburns", clip_seconds: 3, narrate: true, subtitles: "burn" },
  },
  {
    id: "vertical",
    name: "Vertical short",
    hint: "Portrait 2:3 for phones, narrated, captions on",
    settings: { scene_mode: "llm", width: 512, height: 768, max_scenes: 5, motion: "kenburns", clip_seconds: 2, narrate: true, subtitles: "burn" },
  },
  {
    id: "cinematic",
    name: "Cinematic (AI motion)",
    hint: "LTX-Video animates every scene — best result, slowest",
    settings: { scene_mode: "llm", width: 768, height: 512, max_scenes: 3, motion: "ltx", clip_seconds: 3, narrate: true, subtitles: "burn" },
  },
];

export const matches = (preset: Preset, s: JobSettings) =>
  Object.entries(preset.settings).every(([k, v]) => s[k as keyof JobSettings] === v);

/**
 * Rough wall-clock estimate in seconds, from timings measured on an M2 with 16 GB:
 * SDXL loads once per job (~25 s) then ~5 s per image; LTX-Video ~6.5 min per 3 s clip.
 */
export function estimateSeconds(s: JobSettings, sceneCount: number): number {
  const n = Math.max(1, sceneCount);
  let t = 25 + 5 * n; // images
  if (s.scene_mode === "llm") t += 15;
  if (s.narrate) t += 7 * n;
  if (s.motion === "kenburns") t += n;
  if (s.motion === "ltx") t += 390 * n * Math.max(1, s.clip_seconds / 3);
  if (s.music === "auto" && s.motion !== "none") {
    const videoSeconds = n * (s.narrate ? Math.max(s.clip_seconds, 6) : s.clip_seconds);
    t += 15 + 35 + videoSeconds * 1.2; // style from the LLM, then one ACE-Step take
  }
  if (s.ambience === "auto" && s.motion !== "none") t += 10 + 25; // description from the LLM, then one short loop
  return t;
}

export function formatDuration(seconds: number): string {
  if (seconds < 90) return `about ${Math.round(seconds / 5) * 5} s`;
  return `about ${Math.round(seconds / 60)} min`;
}

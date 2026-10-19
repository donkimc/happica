import { Dispatch, SetStateAction, useState } from "react";
import { Job, uploadImage } from "./api";

export interface SlideItem {
  image: string; // path under outputs/
  text: string;
}

interface Props {
  slides: SlideItem[];
  onChange: Dispatch<SetStateAction<SlideItem[]>>; // functional updates, so quick clicks and slow uploads don't drop slides
  imageLibrary: Job[];
}

const src = (path: string) => `/outputs/${path}`;

/** Build a slideshow: upload photos or pick generated images, order them, and write optional narration per slide. */
export default function SlidesEditor({ slides, onChange, imageLibrary }: Props) {
  const [uploading, setUploading] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const libraryImages = imageLibrary.filter((j) => j.state === "done").flatMap((j) => (j.result?.images ?? []) as string[]);

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    setUploading(files.length);
    const added: SlideItem[] = [];
    for (const file of Array.from(files)) {
      try {
        added.push({ image: (await uploadImage(file)).path, text: "" });
      } catch (e) {
        setError(`${file.name}: ${e instanceof Error ? e.message : e}`);
      }
      setUploading((n) => n - 1);
    }
    onChange((prev) => [...prev, ...added]);
  }

  const update = (i: number, patch: Partial<SlideItem>) => onChange((prev) => prev.map((s, k) => (k === i ? { ...s, ...patch } : s)));
  const move = (i: number, d: number) =>
    onChange((prev) => {
      const next = [...prev];
      [next[i], next[i + d]] = [next[i + d], next[i]];
      return next;
    });

  return (
    <div className="slides">
      <div className="row tight-top">
        <label className="upload-button">
          <input type="file" accept="image/*,.heic,.heif" multiple onChange={(e) => addFiles(e.target.files).then(() => (e.target.value = ""))} />
          {uploading ? `Uploading ${uploading}…` : "+ Add photos"}
        </label>
        <button className="secondary inline" disabled={libraryImages.length === 0} onClick={() => setPicking((p) => !p)}>
          {picking ? "Done picking" : `+ From Image library${libraryImages.length ? "" : " (empty)"}`}
        </button>
        <span className="muted">{slides.length} slide{slides.length === 1 ? "" : "s"} · up to 30</span>
      </div>
      {error && <p className="error">{error}</p>}

      {picking && (
        <div className="picker">
          {libraryImages.map((path) => (
            <button key={path} className="pick" title="Add as a slide" onClick={() => onChange((prev) => [...prev, { image: path, text: "" }])}>
              <img src={src(path)} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}

      {slides.length === 0 && !picking && <p className="muted">Add photos from your Mac or phone, or pick images you made in the Images tab.</p>}
      <ol className="slide-list">
        {slides.map((s, i) => (
          <li key={`${s.image}-${i}`} className="slide">
            <img src={src(s.image)} alt={`Slide ${i + 1}`} />
            <div className="slide-body">
              <textarea
                rows={2}
                value={s.text}
                maxLength={400}
                placeholder="Narration / caption for this slide (optional)"
                onChange={(e) => update(i, { text: e.target.value })}
              />
              <div className="slide-actions">
                <span className="muted">#{i + 1}</span>
                <button className="link" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
                <button className="link" disabled={i === slides.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
                <button className="link danger-link" onClick={() => onChange((prev) => prev.filter((_, k) => k !== i))}>Remove</button>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

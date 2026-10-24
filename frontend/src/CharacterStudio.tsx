import { useState } from "react";
import { cancelJob, createCharacter, createImages, createTurnaround, deleteJob, ImageModel, isActive, Job, outputUrl, Texture, uploadImage, useViewsAsRefs } from "./api";
import { IMAGE_DEFAULTS } from "./ImageStudio";

export interface CharacterSettings {
  name: string;
  photo: string;
  kind: "woman" | "man" | "girl" | "boy" | "person" | "art";
  look: string;
  refs?: string[]; // art: 1–3 pictures of the cartoon character
  style?: string;
}

const ART_STYLE = "cute 2D children's picture book illustration, soft pastel colors, clean thick outlines";
const ART_CHANGES = ["waving happily", "running in a flower meadow", "sleeping under a tree", "in the snow", "eating rice cake", "dancing with friends"];

const CHANGES = ["as a 70-year-old", "as a child", "as a teenager", "with long straight hair", "as a medieval knight", "as an astronaut", "in a 1920s portrait", "as a watercolor painting"];

interface Props {
  characters: Job[];
  images: Job[]; // image jobs, to show each character's changes under it
  upsert: (job: Job) => void;
  remove: (id: string) => void;
  act: (fn: () => Promise<unknown>) => void;
  error: string | null;
}

export default function CharacterStudio({ characters, images, upsert, remove, act, error }: Props) {
  const [form, setForm] = useState<CharacterSettings>({ name: "", photo: "", kind: "woman", look: "" });
  const [consent, setConsent] = useState(false);
  const [art, setArt] = useState(false);
  const [refs, setRefs] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const set = (patch: Partial<CharacterSettings>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <>
      <section className="card" id="character-form">
        <div className="segmented" role="radiogroup" aria-label="Kind of character">
          <button role="radio" aria-checked={!art} className={!art ? "on" : ""} onClick={() => setArt(false)}>Real person (face photo)</button>
          <button role="radio" aria-checked={art} className={art ? "on" : ""} onClick={() => setArt(true)}>Cartoon (reference art)</button>
        </div>
        {art ? (
          <ArtForm refs={refs} setRefs={setRefs} act={act} error={error} onCreate={(f) => act(async () => {
            upsert(await createCharacter({ ...f, kind: "art", photo: "", refs, consent: false }));
            setRefs([]);
          })} />
        ) : (
        <>
        <p className="muted tight">
          Upload one clear photo of a face. Happica learns the face and keeps it in new pictures and videos — older,
          younger, in costume or in any scene.
        </p>
        <div className="row tight-top character-new">
          <label className="upload-button">
            <input
              type="file"
              accept="image/*,.heic,.heif"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setUploading(true);
                act(async () => {
                  try {
                    set({ photo: (await uploadImage(file)).path });
                  } finally {
                    setUploading(false);
                    e.target.value = "";
                  }
                });
              }}
            />
            {uploading ? "Uploading…" : form.photo ? "Change photo" : "+ Upload a face photo"}
          </label>
          {form.photo && <img className="face-preview" src={`/outputs/${form.photo}`} alt="Uploaded face" />}
        </div>
        <div className="row">
          <label>
            Name
            <input value={form.name} maxLength={40} placeholder="Maya" onChange={(e) => set({ name: e.target.value })} />
          </label>
          <label>
            Is a
            <select value={form.kind} onChange={(e) => set({ kind: e.target.value as CharacterSettings["kind"] })}>
              {["woman", "man", "girl", "boy", "person"].map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <label className="grow">
            Look — hair, eyes, skin, anything distinctive
            <input value={form.look} maxLength={300} placeholder="short dark curly hair, freckles, brown eyes" onChange={(e) => set({ look: e.target.value })} />
          </label>
        </div>
        <label className="check consent">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          This is me, or this person agreed to have their face used here.
        </label>
        <div className="row">
          <div className="generate">
            <button
              disabled={!form.photo || !form.name.trim() || !consent}
              onClick={() =>
                act(async () => {
                  upsert(await createCharacter({ ...form, consent }));
                  setForm({ name: "", photo: "", kind: form.kind, look: "" });
                  setConsent(false);
                })
              }
            >
              Create character
            </button>
            <span className="muted">makes a 4-picture character sheet · about 45 s</span>
          </div>
        </div>
        <p className="muted">
          The <strong>look</strong> matters: the face model keeps facial features, and the description keeps hair and other
          details consistent when the character is aged or restyled.
        </p>
        {error && <p className="error">{error}</p>}
        </>
        )}
      </section>

      <section>
        <h2>Characters</h2>
        {characters.length === 0 && <p className="muted">No characters yet.</p>}
        {characters.map((job) => (
          <CharacterCard
            key={job.id}
            job={job}
            changes={images.filter((j) => j.settings.character === job.id)}
            upsert={upsert}
            remove={remove}
            act={act}
          />
        ))}
      </section>
    </>
  );
}

/** A cartoon character from 1–3 pictures of it (IP-Adapter) — for picture-book characters PhotoMaker can't keep. */
function ArtForm({ refs, setRefs, act, error, onCreate }: {
  refs: string[]; setRefs: (r: string[]) => void; act: Props["act"]; error: string | null;
  onCreate: (f: { name: string; look: string; style: string }) => void;
}) {
  const [name, setName] = useState("");
  const [look, setLook] = useState("");
  const [style, setStyle] = useState(ART_STYLE);
  const [uploading, setUploading] = useState(false);
  return (
    <>
      <p className="muted tight">
        Upload 1–3 pictures of a cartoon character (front view on a plain background works best). Scenes that name the
        character are drawn from these pictures, so the same character appears in every scene.
      </p>
      <div className="row tight-top character-new">
        <label className="upload-button">
          <input type="file" accept="image/*,.heic,.heif" multiple disabled={refs.length >= 3}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []).slice(0, 3 - refs.length);
              if (!files.length) return;
              setUploading(true);
              act(async () => {
                try {
                  const added = [];
                  for (const f of files) added.push((await uploadImage(f)).path);
                  setRefs([...refs, ...added]);
                } finally {
                  setUploading(false);
                  e.target.value = "";
                }
              });
            }} />
          {uploading ? "Uploading…" : `+ Add pictures (${refs.length}/3)`}
        </label>
        {refs.map((r) => (
          <button key={r} className="ref-thumb" title="Remove" onClick={() => setRefs(refs.filter((x) => x !== r))}>
            <img className="face-preview" src={`/outputs/${r}`} alt="Reference" />
          </button>
        ))}
      </div>
      <div className="row">
        <label>
          Name
          <input value={name} maxLength={40} placeholder="Ttori" onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="grow">
          Look — colours, shape, clothes
          <input value={look} maxLength={300} placeholder="a tiny baby dokkaebi with sky-blue skin, one yellow horn, tiger-striped vest" onChange={(e) => setLook(e.target.value)} />
        </label>
      </div>
      <label className="style">
        Drawing style
        <input value={style} maxLength={300} onChange={(e) => setStyle(e.target.value)} />
      </label>
      <div className="row">
        <div className="generate">
          <button disabled={!refs.length || !name.trim()} onClick={() => { onCreate({ name: name.trim(), look, style }); setName(""); setLook(""); }}>
            Create character
          </button>
          <span className="muted">makes a 4-picture character sheet · about 1 min</span>
        </div>
      </div>
      <p className="muted">In My script, tick the character under <strong>Cartoon characters</strong> and write its name in the pictures where it appears.</p>
      {error && <p className="error">{error}</p>}
    </>
  );
}

function CharacterCard({ job, changes, upsert, remove, act }: { job: Job; changes: Job[]; upsert: Props["upsert"]; remove: Props["remove"]; act: Props["act"] }) {
  const s = job.settings as unknown as CharacterSettings;
  const [change, setChange] = useState("");
  const [texture, setTexture] = useState<Texture | "">("");
  const [quality, setQuality] = useState<ImageModel>("turbo"); // real people: "realvis" draws better faces, ~3× slower
  const active = isActive(job);
  const sheet = (job.result?.images ?? []) as string[];
  const ready = job.state === "done";

  return (
    <article className="card song">
      <div className="row between">
        <div className="character-head">
          <img className="face-preview" src={outputUrl(job, s.photo)} alt={s.name} />
          <div>
            <strong>{s.name}</strong>
            <div className="muted">
              {s.kind}
              {s.look ? ` · ${s.look}` : ""}
            </div>
            <div className="muted">
              {s.kind === "art"
                ? `Cartoon character from ${s.refs?.length ?? 1} picture(s). Tick ${s.name} under Cartoon characters in My script, or pick them in Images.`
                : `Use “${s.name}” in a Story Video scene, or pick ${s.name} in Images.`}
            </div>
          </div>
        </div>
        <span className={`state ${job.state}`}>{job.state === "done" ? "" : job.state}</span>
      </div>
      {active && (
        <div className="progress">
          <span>{job.stage || "waiting"}</span>
          <progress value={job.progress} max={1} />
        </div>
      )}
      {job.error && <p className="error">{job.error}</p>}
      {sheet.length > 0 && (
        <div className="grid">
          {sheet.map((src) => (
            <a key={src} href={outputUrl(job, src)} target="_blank" rel="noreferrer">
              <img src={outputUrl(job, src)} alt={`${s.name} character sheet`} />
            </a>
          ))}
        </div>
      )}

      {ready && (
        <>
          <div className="row">
            <label className="grow">
              Change or place {s.name}
              <input value={change} placeholder={`${s.name} as a 70-year-old, smiling`} onChange={(e) => setChange(e.target.value)} />
            </label>
            <button
              disabled={!change.trim()}
              onClick={() =>
                act(async () => {
                  const prompt = change.toLowerCase().includes(s.name.toLowerCase()) ? change : `${s.name} ${change}`;
                  const style = s.kind === "art" ? s.style || ART_STYLE : "photorealistic, 35mm photo";
                  // real faces at 768 px: at 512 the eyes are tiny and often come out mismatched
                  const size = s.kind === "art" ? 512 : 768;
                  upsert(await createImages({ ...IMAGE_DEFAULTS, prompt, width: size, height: size, count: 2, style, character: job.id,
                                              texture: texture || null, image_model: s.kind === "art" ? "turbo" : quality }));
                  setChange("");
                })
              }
            >
              Create
            </button>
          </div>
          <div className="row tight">
            <label>
              Texture
              <select value={texture} onChange={(e) => setTexture(e.target.value as Texture | "")} title="인물 질감: skin, hair and fabric">
                <option value="">As drawn</option>
                <option value="smooth">Smooth (soft skin)</option>
                <option value="detailed">Detailed (sharp, fine grain)</option>
              </select>
            </label>
            {s.kind !== "art" && (
              <label>
                Quality
                <select value={quality} onChange={(e) => setQuality(e.target.value as ImageModel)}>
                  <option value="turbo">Fast (~35 s a picture)</option>
                  <option value="realvis">Quality — RealVisXL, better faces (~2 min a picture)</option>
                </select>
              </label>
            )}
            <button className="secondary" onClick={() => act(async () => upsert(await createTurnaround(job.id, texture || null, s.kind === "art" ? "turbo" : quality)))}
                    title={s.kind === "art" ? "Front, side and back views (about 2 min)" : "Front, side and back views with a pose guide: ~8 min (Fast) or ~16 min (Quality)"}>
              3-view sheet (3면도)
            </button>
          </div>
          <div className="suggestion-group quick">
            {(s.kind === "art" ? ART_CHANGES : CHANGES).map((c) => (
              <button key={c} className="chip small" onClick={() => setChange(`${s.name} ${c}`)}>
                {c}
              </button>
            ))}
          </div>
        </>
      )}

      {changes.map((cj) => (
        <div key={cj.id} className="change">
          <div className="muted">
            {cj.settings.prompt as string} {isActive(cj) ? `— ${cj.stage || "waiting"}…` : ""}
            {cj.error ? <span className="error"> {cj.error}</span> : null}
          </div>
          {(cj.result as unknown as { turnaround?: boolean })?.turnaround ? (
            <>
              {((cj.result?.images ?? []) as string[]).filter((src) => !/_(front|side|back)\.png$/.test(src)).map((src) => (
                <a key={src} href={outputUrl(cj, src)} target="_blank" rel="noreferrer">
                  <img className="turnaround" src={outputUrl(cj, src)} alt={`${s.name} front, side and back`} />
                </a>
              ))}
              {s.kind === "art" && cj.state === "done" && (
                <button className="link" onClick={() => act(async () => upsert(await useViewsAsRefs(job.id, cj.id)))}
                        title="Scenes will be drawn from these three views">
                  Use these views as {s.name}'s reference pictures
                </button>
              )}
            </>
          ) : (
            <div className="grid">
              {((cj.result?.images ?? []) as string[]).map((src) => (
                <a key={src} href={outputUrl(cj, src)} target="_blank" rel="noreferrer">
                  <img src={outputUrl(cj, src)} alt={cj.settings.prompt as string} />
                </a>
              ))}
            </div>
          )}
        </div>
      ))}

      <div className="actions">
        {active ? (
          <button className="secondary" onClick={() => act(async () => upsert(await cancelJob(job.id)))}>Cancel</button>
        ) : (
          <button
            className="secondary danger"
            onClick={() =>
              confirm(`Delete ${s.name}? Their pictures stay in the Image library.`) &&
              act(async () => {
                await deleteJob(job.id);
                remove(job.id);
              })
            }
          >
            Delete character
          </button>
        )}
      </div>
    </article>
  );
}

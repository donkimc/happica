import { useState } from "react";
import { cancelJob, createCharacter, createImages, deleteJob, isActive, Job, outputUrl, uploadImage } from "./api";
import { IMAGE_DEFAULTS } from "./ImageStudio";

export interface CharacterSettings {
  name: string;
  photo: string;
  kind: "woman" | "man" | "girl" | "boy" | "person";
  look: string;
}

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
  const [uploading, setUploading] = useState(false);
  const set = (patch: Partial<CharacterSettings>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <>
      <section className="card" id="character-form">
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

function CharacterCard({ job, changes, upsert, remove, act }: { job: Job; changes: Job[]; upsert: Props["upsert"]; remove: Props["remove"]; act: Props["act"] }) {
  const s = job.settings as unknown as CharacterSettings;
  const [change, setChange] = useState("");
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
            <div className="muted">Use “{s.name}” in a Story Video scene, or pick {s.name} in Images.</div>
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
                  upsert(await createImages({ ...IMAGE_DEFAULTS, prompt, width: 512, height: 512, count: 2, style: "photorealistic, 35mm photo", character: job.id }));
                  setChange("");
                })
              }
            >
              Create
            </button>
          </div>
          <div className="suggestion-group quick">
            {CHANGES.map((c) => (
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
          <div className="grid">
            {((cj.result?.images ?? []) as string[]).map((src) => (
              <a key={src} href={outputUrl(cj, src)} target="_blank" rel="noreferrer">
                <img src={outputUrl(cj, src)} alt={cj.settings.prompt as string} />
              </a>
            ))}
          </div>
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

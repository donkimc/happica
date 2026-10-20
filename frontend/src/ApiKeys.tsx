import { useEffect, useState } from "react";
import { ApiKey, createApiKey, listApiKeys, revokeApiKey } from "./api";

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

/** Create and revoke API keys for other apps (REST API v1). */
export default function ApiKeys({ onClose }: { onClose: () => void }) {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => listApiKeys().then(setKeys).catch((e) => setError(String(e.message ?? e)));
  useEffect(() => {
    refresh();
  }, []);

  async function create() {
    setError(null);
    try {
      const k = await createApiKey(name.trim());
      setFresh({ name: k.name, key: k.key });
      setCopied(false);
      setName("");
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function copy() {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh.key);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="card api-keys">
      <div className="row between">
        <strong>API keys — let other apps use Happica</strong>
        <button className="link" onClick={onClose}>Close</button>
      </div>
      <p className="muted tight">
        Other apps send a key with each request (<code>Authorization: Bearer hap_…</code>) to make videos, images, music,
        sound effects and voiceovers. See the{" "}
        <a href="/api/v1/docs" target="_blank" rel="noreferrer">interactive API docs</a> and the{" "}
        <a href="/guide.html#api" target="_blank" rel="noreferrer">guide</a>.
      </p>

      {fresh && (
        <div className="new-key">
          <div>
            New key for <strong>{fresh.name}</strong> — copy it now, it won't be shown again:
          </div>
          <code className="key">{fresh.key}</code>
          <div className="row tight">
            <button onClick={copy}>{copied ? "Copied ✓" : "Copy key"}</button>
            <button className="secondary" onClick={() => setFresh(null)}>Done</button>
          </div>
        </div>
      )}

      <div className="row">
        <label className="grow">
          New key name
          <input value={name} maxLength={60} placeholder="e.g. Batch video app" onChange={(e) => setName(e.target.value)} />
        </label>
        <button disabled={!name.trim()} onClick={create}>Create key</button>
      </div>
      {error && <p className="error">{error}</p>}

      {keys.length === 0 ? (
        <p className="muted">No keys yet.</p>
      ) : (
        <table className="keys">
          <thead>
            <tr>
              <th>Name</th>
              <th>Key</th>
              <th>Created</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id}>
                <td>{k.name}</td>
                <td><code>{k.hint}</code></td>
                <td>{when(k.created)}</td>
                <td>{when(k.last_used)}</td>
                <td>
                  <button
                    className="link danger-link"
                    onClick={() =>
                      confirm(`Revoke "${k.name}"? Apps using it will stop working.`) &&
                      revokeApiKey(k.id).then(refresh).catch((e) => setError(String(e.message ?? e)))
                    }
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

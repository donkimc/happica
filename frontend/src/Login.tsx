import { FormEvent, useState } from "react";
import { login } from "./api";

export default function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(password);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <form className="card login" onSubmit={submit}>
        <h1>Happica</h1>
        <p className="muted">Your private AI studio. Enter the password to continue.</p>
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={busy || !password}>
          {busy ? "Checking…" : "Log in"}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  );
}

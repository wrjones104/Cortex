import { useEffect, useState, type FormEvent } from "react";
import {
  connectionOf,
  createOwner,
  deviceLabel,
  discoverBaseUrl,
  errorMessage,
  fetchAuthState,
  isApiError,
  saveConnection,
  signIn,
  type AuthState,
  type Connection,
} from "../lib/api";
import { Notice, Spinner } from "../components/ui";
import { SignInHero } from "../components/Illustrations";

/**
 * Getting in.
 */
export function SignIn({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [state, setState] = useState<AuthState | null>(null);
  const [looking, setLooking] = useState(true);
  const [showAddress, setShowAddress] = useState(false);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [token, setToken] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLooking(true);
      const found = (await discoverBaseUrl()) ?? window.location.origin;
      if (cancelled) return;
      setBaseUrl(found);

      try {
        const answer = await fetchAuthState(found);
        if (!cancelled) setState(answer);
      } catch (cause) {
        if (!cancelled) {
          setError(errorMessage(cause));
          setShowAddress(true);
        }
      } finally {
        if (!cancelled) setLooking(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  async function recheck(address: string) {
    setError(null);
    setBusy(true);
    try {
      setState(await fetchAuthState(address));
    } catch (cause) {
      setError(errorMessage(cause));
      setState(null);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    const creating = state?.configured === false;
    if (creating && password !== confirm) {
      setError("Those two passwords are not the same.");
      return;
    }

    setBusy(true);
    try {
      const session = creating
        ? await createOwner(
            baseUrl,
            { username: username.trim(), password, device: deviceLabel() },
            state?.requires_token ? token.trim() : undefined,
          )
        : await signIn(baseUrl, username.trim(), password, deviceLabel());

      const connection = connectionOf(baseUrl, session);
      saveConnection(connection);
      onConnected(connection);
    } catch (cause) {
      setError(errorMessage(cause));
      if (isApiError(cause) && cause.status === 409) void recheck(baseUrl);
    } finally {
      setBusy(false);
    }
  }

  if (looking) {
    return (
      <div className="center-screen">
        <div className="card stack" style={{ alignItems: "center", textAlign: "center", padding: 32 }}>
          <SignInHero size={80} />
          <Spinner label="Connecting to your Cortex..." />
        </div>
      </div>
    );
  }

  const creating = state?.configured === false;

  return (
    <div className="center-screen">
      <form className="card stack signin-card" onSubmit={submit}>
        <div style={{ textAlign: "center" }}>
          <SignInHero size={88} />
          <h1>{creating ? "Welcome to Cortex! ✨" : "Welcome Back! 👋"}</h1>
          <p style={{ color: "var(--muted)", fontSize: "0.92rem", margin: "6px 0 0" }}>
            {creating
              ? state?.adopting_existing_vault
                ? "This Cortex already holds notes. The account you make now becomes its owner."
                : "Create your owner account to start organizing notes and chatting with your Librarian."
              : "Your notes, your local models, your personal digital brain."}
          </p>
        </div>

        <div>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder={creating ? "lowercase, no spaces" : "Your username"}
            required
            autoFocus
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoComplete="username"
          />
        </div>

        <div>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={creating ? "At least 8 characters" : "Your password"}
            required
            autoComplete={creating ? "new-password" : "current-password"}
          />
        </div>

        {creating && (
          <div>
            <label htmlFor="confirm">Password again</label>
            <input
              id="confirm"
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              required
              autoComplete="new-password"
            />
          </div>
        )}

        {creating && state?.requires_token && (
          <div>
            <label htmlFor="token">Machine token</label>
            <input
              id="token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="Paste the output of cortex token"
              required
              autoComplete="off"
            />
            <p className="hint" style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
              Run <code>cortex token</code> on the machine hosting Cortex to claim this vault.
            </p>
          </div>
        )}

        {error && <Notice kind="error">{error}</Notice>}

        <button
          className="primary bouncy-btn send-btn"
          type="submit"
          disabled={busy || !username.trim() || !password}
          style={{ width: "100%", justifyContent: "center" }}
        >
          {busy ? (
            <Spinner label={creating ? "Setting up..." : "Signing in..."} />
          ) : creating ? (
            "Create Account & Start 🚀"
          ) : (
            "Sign In & Open Vault 🚀"
          )}
        </button>

        {showAddress ? (
          <div style={{ marginTop: 8 }}>
            <label htmlFor="baseUrl">Server address</label>
            <div className="row" style={{ gap: 8 }}>
              <input
                id="baseUrl"
                type="url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="http://127.0.0.1:8765"
                autoComplete="url"
              />
              <button className="bouncy-btn" type="button" onClick={() => void recheck(baseUrl.trim())} disabled={busy}>
                Check
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="link"
            onClick={() => setShowAddress(true)}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              color: "var(--muted)",
              fontSize: "0.82rem",
              cursor: "pointer",
              textAlign: "center",
              marginTop: 4,
            }}
          >
            Connected to {baseUrl}. Change server
          </button>
        )}
      </form>
    </div>
  );
}

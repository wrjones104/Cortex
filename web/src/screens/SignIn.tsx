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

/**
 * Getting in.
 *
 * This used to ask for a server address and a 43-character token, both typed
 * by hand. The address is now found rather than asked for - Cortex serves this
 * app itself, so the page's own origin is almost always the answer - and the
 * token is replaced by a password, which is a thing a person can type on a
 * phone without a password manager and a lot of patience.
 *
 * The address field is still here, behind a disclosure, for the two cases that
 * need it: development, where the client is on a different port from the API,
 * and anyone pointing one client at a Cortex somewhere else.
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

  // Find the server, then ask it whether anybody has an account here yet.
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
      // Someone else claimed it, or an account appeared while this screen was
      // open. Re-read the state rather than leaving the wrong form up.
      if (isApiError(cause) && cause.status === 409) void recheck(baseUrl);
    } finally {
      setBusy(false);
    }
  }

  if (looking) {
    return (
      <div className="center-screen">
        <div className="card stack">
          <Spinner label="Looking for your Cortex..." />
        </div>
      </div>
    );
  }

  const creating = state?.configured === false;

  return (
    <div className="center-screen">
      <form className="card stack" onSubmit={submit}>
        <div>
          <h1>{creating ? "Set up your Cortex" : "Sign in"}</h1>
          <p style={{ color: "var(--muted)", fontSize: "0.92rem", margin: "6px 0 0" }}>
            {creating
              ? state?.adopting_existing_vault
                ? "This Cortex already holds notes. The account you make now becomes its owner, and those notes stay exactly where they are."
                : "Pick a username and a password. This first account is the owner and can add others later."
              : "Your notes, your models, your section of the vault."}
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
              Asked for only this once. There are already notes here, so claiming
              them needs proof you are at the machine serving them. Run{" "}
              <code>cortex token</code> there.
            </p>
          </div>
        )}

        {error && <Notice kind="error">{error}</Notice>}

        <button
          className="primary"
          type="submit"
          disabled={busy || !username.trim() || !password}
        >
          {busy ? (
            <Spinner label={creating ? "Creating..." : "Signing in..."} />
          ) : creating ? (
            "Create account"
          ) : (
            "Sign in"
          )}
        </button>

        {showAddress ? (
          <div>
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
              <button type="button" onClick={() => void recheck(baseUrl.trim())} disabled={busy}>
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
              fontSize: "0.85rem",
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            Connected to {baseUrl}. Change server
          </button>
        )}
      </form>
    </div>
  );
}

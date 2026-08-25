import { useState, type FormEvent } from "react";
import { errorMessage, saveConnection, verifyConnection, type Connection } from "../lib/api";
import { Notice, Spinner } from "../components/ui";

/**
 * First run. The address and token are entered rather than baked in, because
 * the same built app is opened from localhost on the desktop and from a
 * Tailscale address on the phone.
 */
export function Setup({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const [baseUrl, setBaseUrl] = useState(
    () => `${window.location.protocol}//${window.location.hostname}:8765`,
  );
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function connect(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setChecking(true);

    const connection: Connection = { baseUrl: baseUrl.trim(), token: token.trim() };
    try {
      await verifyConnection(connection);
      saveConnection(connection);
      onConnected(connection);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="center-screen">
      <form className="card stack" onSubmit={connect}>
        <div>
          <h1>Connect to your vault</h1>
          <p style={{ color: "var(--muted)", fontSize: "0.92rem", margin: "6px 0 0" }}>
            Start the server with <code>cortex serve</code>, then run{" "}
            <code>cortex token</code> to get your token.
          </p>
        </div>

        <div>
          <label htmlFor="baseUrl">Server address</label>
          <input
            id="baseUrl"
            type="url"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="http://127.0.0.1:8765"
            required
            autoComplete="url"
          />
        </div>

        <div>
          <label htmlFor="token">Token</label>
          <input
            id="token"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Paste the output of cortex token"
            required
            autoComplete="off"
          />
        </div>

        {error && <Notice kind="error">{error}</Notice>}

        <button className="primary" type="submit" disabled={checking || !token.trim()}>
          {checking ? <Spinner label="Checking..." /> : "Connect"}
        </button>
      </form>
    </div>
  );
}

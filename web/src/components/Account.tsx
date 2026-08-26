import { useEffect, useState, type FormEvent } from "react";
import { errorMessage, saveConnection, type Account as AccountRow, type Me } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Notice, Spinner } from "../components/ui";

/**
 * Your account, and - if you are the owner - everybody else's.
 *
 * Kept in Settings rather than behind its own nav item. Signing out and
 * changing a password are rare enough that a fifth icon would cost more than
 * it pays, and the connection details this replaces already lived here.
 */
export function Account() {
  const { api, baseUrl, disconnect } = useApi();

  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.me().then(setMe, (cause) => setError(errorMessage(cause)));
  }, [api]);

  async function signOut() {
    // Tell the server first so the session is actually revoked rather than
    // just forgotten locally, but sign out either way - being unable to
    // reach the server is no reason to stay signed in on this device.
    try {
      await api.logout();
    } catch {
      /* the local session goes regardless */
    }
    disconnect();
  }

  async function signOutEverywhere() {
    if (!confirm("Sign out every device, including this one?")) return;
    try {
      await api.revokeSessions();
    } catch (cause) {
      setError(errorMessage(cause));
      return;
    }
    disconnect();
  }

  return (
    <section className="stack">
      <h2>Account</h2>

      {error && <Notice kind="error">{error}</Notice>}
      {note && <Notice kind="ok">{note}</Notice>}
      {!me && !error && <Spinner label="Loading..." />}

      {me?.needs_account && (
        <Notice kind="warn">
          <div className="stack" style={{ gap: 6 }}>
            <strong>This Cortex has no accounts yet.</strong>
            <span>
              You are signed in with the machine token, so everything works, but
              nothing is separated. Run <code>cortex user add &lt;name&gt;</code> on
              the machine serving Cortex, or sign out and set one up here.
            </span>
          </div>
        </Notice>
      )}

      {me && !me.needs_account && (
        <div className="card stack" style={{ gap: 10, fontSize: "0.9rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span style={{ color: "var(--muted)" }}>Signed in as</span>
            <span style={{ fontWeight: 600 }}>
              {me.user.display_name || me.user.username}
              {me.user.is_owner && (
                <span style={{ color: "var(--muted)", fontWeight: 400 }}> · owner</span>
              )}
            </span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span style={{ color: "var(--muted)" }}>Devices signed in</span>
            <span style={{ fontWeight: 600 }}>{me.sessions}</span>
          </div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: "0.74rem",
              color: "var(--faint)",
              wordBreak: "break-all",
            }}
          >
            {baseUrl}
          </div>
        </div>
      )}

      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button type="button" onClick={() => void signOut()}>
          Sign out
        </button>
        {me && !me.needs_account && me.sessions > 1 && (
          <button type="button" onClick={() => void signOutEverywhere()}>
            Sign out everywhere
          </button>
        )}
      </div>

      {me && !me.needs_account && <ChangePassword onDone={setNote} onError={setError} />}
      {me?.user.is_owner && !me.needs_account && <People />}
    </section>
  );
}

/**
 * Changing your own password.
 *
 * The server ends every session, this one included, and returns a fresh token
 * for the device that did it. Saving that token is what stops the act of
 * securing your account from immediately logging you out of it.
 */
function ChangePassword({
  onDone,
  onError,
}: {
  onDone: (message: string) => void;
  onError: (message: string | null) => void;
}) {
  const { api, baseUrl } = useApi();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmed, setConfirmed] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    onError(null);

    if (next !== confirmed) {
      onError("Those two passwords are not the same.");
      return;
    }

    setBusy(true);
    try {
      const session = await api.changePassword(current, next);
      saveConnection({
        baseUrl,
        token: session.token,
        username: session.user.username,
        displayName: session.user.display_name,
        isOwner: session.user.is_owner,
        expiresAt: session.expires_at,
      });
      setOpen(false);
      setCurrent("");
      setNext("");
      setConfirmed("");
      onDone("Password changed. Every other device has been signed out.");
      // The stored token changed under the running app; reload so every
      // request from here on uses the new one.
      window.location.reload();
    } catch (cause) {
      onError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div>
        <button type="button" onClick={() => setOpen(true)}>
          Change password
        </button>
      </div>
    );
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <div>
        <label htmlFor="current">Current password</label>
        <input
          id="current"
          type="password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          required
          autoComplete="current-password"
        />
      </div>
      <div>
        <label htmlFor="next">New password</label>
        <input
          id="next"
          type="password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          placeholder="At least 8 characters"
          required
          autoComplete="new-password"
        />
      </div>
      <div>
        <label htmlFor="again">New password again</label>
        <input
          id="again"
          type="password"
          value={confirmed}
          onChange={(event) => setConfirmed(event.target.value)}
          required
          autoComplete="new-password"
        />
      </div>
      <p style={{ margin: 0, fontSize: "0.84rem", color: "var(--muted)" }}>
        Every other device will be signed out.
      </p>
      <div className="row" style={{ gap: 8 }}>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? <Spinner label="Changing..." /> : "Change password"}
        </button>
        <button type="button" onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Everyone with an account here. Owner only. */
function People() {
  const { api, account } = useApi();

  const [people, setPeople] = useState<AccountRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.accounts().then(setPeople, (cause) => setError(errorMessage(cause)));

  useEffect(() => {
    void load();
  }, [api]);

  async function add(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.createAccount({ username: username.trim(), password });
      setUsername("");
      setPassword("");
      setAdding(false);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove(person: AccountRow) {
    // Two questions rather than one, because the second is the irreversible
    // half and burying it inside a single "are you sure" is how people delete
    // things they meant to keep.
    if (!confirm(`Remove ${person.username}'s account? They will be signed out.`)) return;
    const purge = confirm(
      `Also delete ${person.username}'s notes?\n\n` +
        "OK deletes their whole vault permanently.\n" +
        "Cancel keeps the file on disk, so it can be restored.",
    );

    setError(null);
    try {
      await api.deleteAccount(person.id, purge);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  return (
    <div className="stack" style={{ gap: 10 }}>
      <h3 style={{ margin: 0, fontSize: "0.95rem" }}>People</h3>
      <p style={{ margin: 0, fontSize: "0.84rem", color: "var(--muted)" }}>
        Everyone here gets their own notes, their own conversations and their own
        model settings. Nobody can see anyone else's.
      </p>

      {error && <Notice kind="error">{error}</Notice>}
      {!people && !error && <Spinner label="Loading..." />}

      {people && (
        <div className="card stack" style={{ gap: 8, fontSize: "0.9rem" }}>
          {people.map((person) => (
            <div
              key={person.id}
              style={{ display: "flex", justifyContent: "space-between", gap: 12 }}
            >
              <span>
                {person.display_name || person.username}
                {person.is_owner && (
                  <span style={{ color: "var(--muted)" }}> · owner</span>
                )}
                {person.username === account.username && (
                  <span style={{ color: "var(--muted)" }}> · you</span>
                )}
              </span>
              {!person.is_owner && (
                <button type="button" onClick={() => void remove(person)}>
                  Remove
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <form className="card stack" onSubmit={add}>
          <div>
            <label htmlFor="new-username">Username</label>
            <input
              id="new-username"
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="lowercase, no spaces"
              required
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>
          <div>
            <label htmlFor="new-password">Password to start with</label>
            <input
              id="new-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="At least 8 characters"
              required
              autoComplete="new-password"
            />
            <p style={{ margin: "6px 0 0", fontSize: "0.84rem", color: "var(--muted)" }}>
              Tell them this password. They can change it once they are in.
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="primary" type="submit" disabled={busy}>
              {busy ? <Spinner label="Creating..." /> : "Add person"}
            </button>
            <button type="button" onClick={() => setAdding(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div>
          <button type="button" onClick={() => setAdding(true)}>
            Add someone
          </button>
        </div>
      )}
    </div>
  );
}

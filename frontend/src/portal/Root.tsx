/* ─────────────────────────────────────────────────────────────
 * Portal root: landing page and login, then the admin console or
 * the customer portal depending on the signed-in user's role.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import App from "../App";
import { get, post } from "./api";
import type { SessionUser } from "./api";
import AdminApp from "./AdminApp";
import ClientApp from "./ClientApp";
import Landing, { Logo } from "./Landing";
import { Button, Field, Input, Message, useHashRoute } from "./ui";

const LOGIN_OPTIONS = [
  { key: "customer", label: "Customer", role: "client" },
  { key: "admin", label: "Admin", role: "admin" },
];

function Login({ option, onChoose, onHome, onSignedIn }: {
  option: string;
  onChoose: (key: string) => void;
  onHome: () => void;
  onSignedIn: (user: SessionUser) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const selected = LOGIN_OPTIONS.find((o) => o.key === option) ?? LOGIN_OPTIONS[0];

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await post<SessionUser>("/auth/login", { email, password, role: selected.role }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col items-center justify-center gap-6 px-4 py-8">
      <button onClick={onHome} aria-label="NavyaVoice home"><Logo /></button>
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-gray-800 bg-gray-900/60 p-6">
        <h1 className="text-lg font-bold text-white">Log in</h1>
        <div role="radiogroup" aria-label="Login type" className="grid grid-cols-2 gap-1 rounded-lg bg-gray-950 p-1">
          {LOGIN_OPTIONS.map((o) => (
            <button key={o.key} type="button" role="radio" aria-checked={selected.key === o.key}
              onClick={() => { setError(null); onChoose(o.key); }}
              className={`rounded-md py-1.5 text-sm font-medium transition-colors ${
                selected.key === o.key ? "bg-indigo-600 text-white" : "text-gray-400 hover:text-white"
              }`}>
              {o.label}
            </button>
          ))}
        </div>
        <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
        <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        <Message error={error} />
        <Button type="submit" disabled={busy} className="w-full">{busy ? "Logging in…" : `Log in as ${selected.label.toLowerCase()}`}</Button>
      </form>
      <button onClick={onHome} className="text-sm text-gray-400 hover:text-white">Back to home</button>
    </div>
  );
}

export default function Root() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [checked, setChecked] = useState(false);
  const [route, navigate] = useHashRoute("overview");

  useEffect(() => {
    get<SessionUser>("/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setChecked(true));
    const expired = () => {
      setUser(null);
      window.location.hash = "#/login";
    };
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);

  // A signed-in user has no use for the login page
  useEffect(() => {
    if (user && route.startsWith("login")) navigate("overview");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, route]);

  async function signOut() {
    await post("/auth/logout");
    setUser(null);
    window.location.hash = "";
  }

  if (!checked) return <div className="min-h-screen bg-gray-950" />;
  if (!user) {
    if (route.startsWith("login")) {
      return <Login option={route.split("/")[1] ?? "customer"} onChoose={(key) => navigate(`login/${key}`)}
        onHome={() => { window.location.hash = ""; }} onSignedIn={setUser} />;
    }
    return <Landing onLogin={() => navigate("login/customer")} />;
  }
  if (route.startsWith("login")) return <div className="min-h-screen bg-gray-950" />;

  if (user.role === "client") {
    return <ClientApp title={user.client_name ?? "Your account"} subtitle={user.email} onSignOut={signOut} />;
  }
  if (route === "console") {
    return (
      <div className="relative">
        <App />
        <button onClick={() => navigate("overview")}
          className="fixed bottom-4 left-4 z-10 rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800">
          Back to admin
        </button>
      </div>
    );
  }
  const viewing = route.match(/^client\/([^/]+)/);
  if (viewing) {
    return (
      <ClientApp key={viewing[1]} clientId={viewing[1]} title="Viewing as customer" subtitle={viewing[1]} onSignOut={signOut}
        onExit={() => navigate(`client-admin/${viewing[1]}`)} />
    );
  }
  return <AdminApp email={user.email} onSignOut={signOut} onOpenClient={(c) => navigate(`client/${c.id}/overview`)} />;
}

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
import type { ReactNode } from "react";
import Landing, { Logo } from "./Landing";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { Button, Field, Input, Message, useHashRoute } from "./ui";

const SERVICES: { icon: IconName; title: string; text: string }[] = [
  { icon: "mic", title: "AI calling agent", text: "Calls your leads in natural Hindi or English, and says it is an AI." },
  { icon: "calendar", title: "Demos and meetings booked", text: "Interested customers get a demo or a meeting with your team." },
  { icon: "fileCheck", title: "Approved answers only", text: "Prices and promises come only from the details you approve." },
  { icon: "list", title: "Results for every call", text: "Transcript, summary and a clear result, so you know who to call next." },
  { icon: "shield", title: "DND and opt-outs handled", text: "Blocked numbers and calling hours are checked before every dial." },
];

/** Login and sign-up: Navix branding and services on the left, the form on the right (stacked on phones). */
function AuthLayout({ onHome, children }: { onHome: () => void; children: ReactNode }) {
  return (
    <div className="min-h-screen grid gap-6 p-4 lg:grid-cols-[1.1fr_1fr] lg:gap-10 lg:p-6">
      <aside className="rise flex flex-col justify-between gap-8 rounded-[2rem] bg-neutral-900 p-7 text-white sm:p-10">
        <button onClick={onHome} aria-label="Navix AI home" className="self-start"><Logo /></button>
        <div>
          <h2 className="max-w-md text-3xl font-medium leading-tight tracking-[-0.03em] sm:text-4xl">
            Sales calls that sound like a person, <span className="text-lime-300">not a script</span>
          </h2>
          <ul className="mt-8 hidden gap-5 sm:grid">
            {SERVICES.map((s, i) => (
              <li key={s.title} className="rise flex gap-4" style={{ "--d": `${150 + i * 90}ms` } as React.CSSProperties}>
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white/10 text-lime-300">
                  <Icon name={s.icon} className="h-5 w-5" />
                </span>
                <div>
                  <p className="font-medium">{s.title}</p>
                  <p className="text-sm text-neutral-400">{s.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-sm text-neutral-400">Pay only for answered minutes. Every call opens by saying it is an AI assistant.</p>
      </aside>
      <main className="flex flex-col items-center justify-center gap-6 py-4">
        {children}
        <button onClick={onHome} className="text-sm text-gray-600 hover:text-gray-900">Back to home</button>
      </main>
    </div>
  );
}

const LOGIN_OPTIONS = [
  { key: "customer", label: "Customer", role: "client" },
  { key: "admin", label: "Admin", role: "admin" },
];

/** Free trial sign-up: email, password and a phone number the team can reach. */
function SignUp({ onHome, onLogin, onSignedIn }: { onHome: () => void; onLogin: () => void; onSignedIn: (user: SessionUser) => void }) {
  const [form, setForm] = useState({ email: "", password: "", phone_number: "", website: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await post<SessionUser>("/auth/signup", form));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout onHome={onHome}>
      <form onSubmit={submit} className="pop w-full max-w-sm space-y-4 glass glass-liquid glass-frost rounded-3xl p-7">
        <div>
          <h1 className="text-2xl font-medium tracking-tight text-gray-900">Start your free trial</h1>
          <p className="mt-1 text-sm text-gray-600">50 free minutes to talk to your own agent in the browser.</p>
        </div>
        <Field label="Email"><Input type="email" autoComplete="email" value={form.email} onChange={set("email")} required /></Field>
        <Field label="Password" hint="At least 10 characters"><Input type="password" autoComplete="new-password" value={form.password} onChange={set("password")} required minLength={10} /></Field>
        <Field label="Phone number" hint="We'll reach you here to set up your agent"><Input type="tel" autoComplete="tel" value={form.phone_number} onChange={set("phone_number")} required placeholder="98765 43210" /></Field>
        <input type="text" name="website" value={form.website} onChange={set("website")} tabIndex={-1} autoComplete="off"
          aria-hidden="true" className="absolute -left-[9999px] h-px w-px opacity-0" />
        <Message error={error} />
        <Button type="submit" disabled={busy} className="btn-bounce w-full">{busy ? "Creating your account…" : "Sign up"}</Button>
        <p className="text-center text-sm text-gray-600">
          Already have an account? <button type="button" onClick={onLogin} className="font-medium text-violet-700 underline">Log in</button>
        </p>
      </form>
    </AuthLayout>
  );
}

function Login({ option, onChoose, onHome, onSignedIn, onSignUp }: {
  option: string;
  onChoose: (key: string) => void;
  onHome: () => void;
  onSignedIn: (user: SessionUser) => void;
  onSignUp: () => void;
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
    <AuthLayout onHome={onHome}>
      <form onSubmit={submit} className="pop w-full max-w-sm space-y-4 glass glass-liquid glass-frost rounded-3xl p-7">
        <h1 className="text-2xl font-medium tracking-tight text-gray-900">Log in</h1>
        <div role="radiogroup" aria-label="Login type" className="grid grid-cols-2 gap-1 rounded-full bg-white/70 p-1">
          {LOGIN_OPTIONS.map((o) => (
            <button key={o.key} type="button" role="radio" aria-checked={selected.key === o.key}
              onClick={() => { setError(null); onChoose(o.key); }}
              className={`rounded-full py-1.5 text-sm font-medium transition-colors ${
                selected.key === o.key ? "bg-neutral-900 text-white" : "text-gray-600 hover:text-gray-900"
              }`}>
              {o.label}
            </button>
          ))}
        </div>
        <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
        <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        <Message error={error} />
        <Button type="submit" disabled={busy} className="w-full">{busy ? "Logging in…" : `Log in as ${selected.label.toLowerCase()}`}</Button>
        {selected.role === "client" && (
          <p className="text-center text-sm text-gray-600">
            New here? <button type="button" onClick={onSignUp} className="font-medium text-violet-700 underline">Start a free trial</button>
          </p>
        )}
      </form>
    </AuthLayout>
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
    if (user && (route.startsWith("login") || route === "signup")) navigate("overview");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, route]);

  async function signOut() {
    await post("/auth/logout");
    setUser(null);
    window.location.hash = "";
  }

  if (!checked) return <div className="min-h-screen" />;
  if (!user) {
    if (route === "signup") {
      return <SignUp onHome={() => { window.location.hash = ""; }} onLogin={() => navigate("login/customer")} onSignedIn={setUser} />;
    }
    if (route.startsWith("login")) {
      return <Login option={route.split("/")[1] ?? "customer"} onChoose={(key) => navigate(`login/${key}`)}
        onHome={() => { window.location.hash = ""; }} onSignedIn={setUser} onSignUp={() => navigate("signup")} />;
    }
    return <Landing onLogin={() => navigate("login/customer")} onSignUp={() => navigate("signup")} />;
  }
  if (route.startsWith("login") || route === "signup") return <div className="min-h-screen" />;

  if (user.role === "client") {
    return <ClientApp title={user.client_name ?? "Your account"} subtitle={user.email} onSignOut={signOut} />;
  }
  if (route === "console") {
    return (
      <div className="relative">
        <App />
        <button onClick={() => navigate("overview")}
          className="fixed bottom-4 left-4 z-10 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-800 hover:bg-black/5">
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

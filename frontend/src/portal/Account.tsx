/* ─────────────────────────────────────────────────────────────
 * Account page for clients and admins: change password.
 * Changing it signs out every other device.
 * ─────────────────────────────────────────────────────────── */
import { useState } from "react";
import type { FormEvent } from "react";
import { post } from "./api";
import { Button, Card, Field, Input, Message } from "./ui";

export default function Account({ email }: { email?: string }) {
  const blank = { current_password: "", new_password: "", confirm: "" };
  const [form, setForm] = useState(blank);
  const [feedback, setFeedback] = useState<{ error?: string; success?: string }>({});
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (form.new_password !== form.confirm) {
      setFeedback({ error: "The new passwords don't match." });
      return;
    }
    try {
      await post("/auth/password", { current_password: form.current_password, new_password: form.new_password });
      setForm(blank);
      setFeedback({ success: "Password changed. You stay signed in here; other devices have been signed out." });
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  return (
    <Card title="Change password">
      {email && <p className="mb-3 text-sm text-gray-600">Signed in as {email}.</p>}
      <form onSubmit={submit} className="grid max-w-md gap-3">
        <Message error={feedback.error} success={feedback.success} />
        <Field label="Current password">
          <Input type="password" value={form.current_password} onChange={set("current_password")} required autoComplete="current-password" />
        </Field>
        <Field label="New password" hint="At least 10 characters">
          <Input type="password" value={form.new_password} onChange={set("new_password")} required minLength={10} autoComplete="new-password" />
        </Field>
        <Field label="Confirm new password">
          <Input type="password" value={form.confirm} onChange={set("confirm")} required minLength={10} autoComplete="new-password" />
        </Field>
        <div><Button type="submit">Change password</Button></div>
      </form>
    </Card>
  );
}

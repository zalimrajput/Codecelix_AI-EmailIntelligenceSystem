"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { supabase } from "@/lib/supabase";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { error: resetError } = await supabase().auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/reset-password`,
      });
      if (resetError) throw resetError;
      setNotice("If an account exists for that email, a password reset link is on its way.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the reset email.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-story">
        <Link href="/" className="brand brand-light"><span className="brand-mark">l.</span> letterwise</Link>
        <div className="story-copy">
          <span className="eyebrow">A FRESH START</span>
          <h1>Your account,<br />back in reach.</h1>
          <p>We’ll send a secure link so you can choose a new password.</p>
        </div>
        <span className="story-footer">A calmer kind of email intelligence</span>
      </section>
      <section className="auth-form-side">
        <div className="auth-form-wrap">
          <Link href="/" className="mobile-brand brand"><span className="brand-mark">l.</span> letterwise</Link>
          <span className="eyebrow">PASSWORD RECOVERY</span>
          <h2>Forgot your password?</h2>
          <p className="muted">Enter the email address associated with your account.</p>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {notice && <div className="alert alert-success" role="status">{notice}</div>}
          <form onSubmit={(event) => void submit(event)} className="auth-form">
            <label>Email address<input type="email" autoComplete="email" maxLength={254} required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>
            <button className="button button-primary button-wide" disabled={busy}>{busy ? "Sending…" : "Send reset link"}<span>→</span></button>
          </form>
          <p className="auth-switch"><Link href="/login">Back to sign in</Link></p>
        </div>
      </section>
    </main>
  );
}

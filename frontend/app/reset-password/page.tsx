"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [busy, setBusy] = useState(true);
  const [saving, setSaving] = useState(false);
  const [recoveryVerified, setRecoveryVerified] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let recoveryDetected = false;
    const client = supabase();
    const { data: listener } = client.auth.onAuthStateChange((event) => {
      if (event === "PASSWORD_RECOVERY" && !cancelled) {
        recoveryDetected = true;
        setRecoveryVerified(true);
        setBusy(false);
      }
    });

    async function establishRecoverySession() {
      try {
        const params = new URLSearchParams(window.location.search);
        const authError = params.get("error_description") || params.get("error");
        if (authError) throw new Error(authError);

        const code = params.get("code");
        const tokenHash = params.get("token_hash");
        if (code) {
          const { error: exchangeError } = await client.auth.exchangeCodeForSession(code);
          if (exchangeError) throw exchangeError;
          recoveryDetected = true;
        } else if (params.get("type") === "recovery" && tokenHash) {
          const { error: verifyError } = await client.auth.verifyOtp({
            token_hash: tokenHash,
            type: "recovery",
          });
          if (verifyError) throw verifyError;
          recoveryDetected = true;
        } else {
          const hash = new URLSearchParams(window.location.hash.slice(1));
          if (hash.get("type") === "recovery") {
            const accessToken = hash.get("access_token");
            const refreshToken = hash.get("refresh_token");
            if (!accessToken || !refreshToken) {
              throw new Error("The reset link is incomplete. Request a new password reset email.");
            }
            const { error: sessionError } = await client.auth.setSession({
              access_token: accessToken,
              refresh_token: refreshToken,
            });
            if (sessionError) throw sessionError;
            recoveryDetected = true;
          }
        }

        if (!recoveryDetected) {
          throw new Error("Open the password reset link from your email, or request a new one.");
        }
        if (!cancelled) setRecoveryVerified(true);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "The reset link is invalid or expired.");
      } finally {
        if (!cancelled) setBusy(false);
      }
    }

    void establishRecoverySession();
    return () => {
      cancelled = true;
      listener.subscription.unsubscribe();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (password !== confirmPassword) {
      setError("The passwords do not match.");
      return;
    }
    setSaving(true);
    try {
      const { error: updateError } = await supabase().auth.updateUser({ password });
      if (updateError) throw updateError;
      const { error: signOutError } = await supabase().auth.signOut({ scope: "local" });
      if (signOutError) throw signOutError;
      router.replace("/login?password=updated");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update your password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-story">
        <Link href="/" className="brand brand-light"><span className="brand-mark">l.</span> letterwise</Link>
        <div className="story-copy">
          <span className="eyebrow">SECURE ACCOUNT RECOVERY</span>
          <h1>Choose a<br />new password.</h1>
          <p>Your password is updated securely through Supabase Auth.</p>
        </div>
        <span className="story-footer">A calmer kind of email intelligence</span>
      </section>
      <section className="auth-form-side">
        <div className="auth-form-wrap">
          <Link href="/" className="mobile-brand brand"><span className="brand-mark">l.</span> letterwise</Link>
          <span className="eyebrow">PASSWORD RECOVERY</span>
          <h2>Set a new password.</h2>
          <p className="muted">Choose a password with at least 8 characters.</p>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {busy ? <div className="alert" role="status">Validating your secure reset link…</div> : recoveryVerified ? (
            <form onSubmit={(event) => void submit(event)} className="auth-form">
              <label>New password<span className="auth-password-field"><input type={showPassword ? "text" : "password"} autoComplete="new-password" minLength={8} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} /><button type="button" className="password-visibility" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword}>{showPassword ? "Hide" : "Show"}</button></span></label>
              <label>Confirm new password<span className="auth-password-field"><input type={showConfirmPassword ? "text" : "password"} autoComplete="new-password" minLength={8} maxLength={128} required value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} /><button type="button" className="password-visibility" onClick={() => setShowConfirmPassword((visible) => !visible)} aria-label={showConfirmPassword ? "Hide confirmed password" : "Show confirmed password"} aria-pressed={showConfirmPassword}>{showConfirmPassword ? "Hide" : "Show"}</button></span></label>
              <button className="button button-primary button-wide" disabled={saving}>{saving ? "Updating…" : "Update password"}<span>→</span></button>
            </form>
          ) : <p className="auth-switch"><Link href="/forgot-password">Request a new reset link</Link></p>}
          {recoveryVerified && <p className="auth-switch"><Link href="/login">Back to sign in</Link></p>}
        </div>
      </section>
    </main>
  );
}

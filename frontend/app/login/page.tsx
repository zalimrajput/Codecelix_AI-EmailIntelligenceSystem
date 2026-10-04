"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [registering, setRegistering] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    const logoutStatus = new URLSearchParams(window.location.search).get("logout");
    if (logoutStatus === "complete") {
      setNotice("You’ve been signed out.");
    } else if (logoutStatus === "local") {
      setNotice("You’ve been signed out on this device. The server could not confirm session revocation.");
    }
    if (new URLSearchParams(window.location.search).get("password") === "updated") {
      setNotice("Your password has been updated. Sign in with your new password.");
    }
    void Promise.resolve()
      .then(() => supabase().auth.getSession())
      .then(({ data }) => { if (!cancelled && data.session) router.replace("/dashboard"); })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Authentication is not configured.");
      });
    return () => { cancelled = true; };
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API_BASE}/auth/${registering ? "register" : "login"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(registering ? { full_name: name, email, password } : { email, password }),
      });
      const result = await response.json() as {
        detail?: unknown;
        access_token?: string;
        refresh_token?: string;
        user?: { email?: string };
      };
      if (!response.ok) {
        const detail = typeof result.detail === "string"
          ? result.detail
          : Array.isArray(result.detail)
            ? result.detail.map((issue: unknown) => {
              if (!issue || typeof issue !== "object") return "";
              const item = issue as { loc?: unknown[]; msg?: unknown };
              const location = Array.isArray(item.loc)
                ? item.loc.filter((part) => typeof part === "string" || typeof part === "number").join(".")
                : "";
              return [location, typeof item.msg === "string" ? item.msg : ""].filter(Boolean).join(": ");
            }).filter(Boolean).join("; ")
            : undefined;
        throw new Error(detail || "Could not authenticate.");
      }
      if (result.access_token && result.refresh_token) {
        const { error: sessionError } = await supabase().auth.setSession({
          access_token: result.access_token,
          refresh_token: result.refresh_token,
        });
        if (sessionError) throw sessionError;
        router.replace("/dashboard");
        return;
      }
      setNotice(
        registering
          ? "Check your inbox to confirm your email, then come back to sign in."
          : "Your account needs email confirmation before you can continue.",
      );
      if (registering) setRegistering(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not authenticate.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-story">
        <Link href="/" className="brand brand-light"><span className="brand-mark">l.</span> letterwise</Link>
        <div className="story-copy">
          <span className="eyebrow">MAKE ROOM FOR WHAT MATTERS</span>
          <h1>Your inbox,<br />with its mind<br />in the right place.</h1>
          <p>Find the signal. See what needs you. Let the busywork take care of itself.</p>
          <div className="story-note"><span className="note-icon">✳</span><span>AI that reads between the lines,<br />so you don&apos;t have to.</span></div>
        </div>
        <span className="story-footer">A calmer kind of email intelligence</span>
      </section>
      <section className="auth-form-side">
        <div className="auth-form-wrap">
          <div className="mobile-brand brand"><span className="brand-mark">l.</span> letterwise</div>
          <span className="eyebrow">YOUR SPACE TO THINK CLEARLY</span>
          <h2>{registering ? "Let’s get you started." : "Welcome back."}</h2>
          <p className="muted">{registering ? "A little more clarity starts right here." : "Your inbox is ready when you are."}</p>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {notice && <div className="alert alert-success" role="status">{notice}</div>}
          <form onSubmit={submit} className="auth-form">
            {registering && <label>Your name<input autoComplete="name" maxLength={255} required value={name} onChange={(e) => setName(e.target.value)} placeholder="Jamie Taylor" /></label>}
            <label>Email address<input type="email" autoComplete="email" maxLength={254} required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" /></label>
            <label>Password<span className="auth-password-field"><input type={showPassword ? "text" : "password"} autoComplete={registering ? "new-password" : "current-password"} minLength={8} maxLength={128} required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" /><button type="button" className="password-visibility" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword}>{showPassword ? "Hide" : "Show"}</button></span></label>
            <button className="button button-primary button-wide" disabled={busy}>{busy ? "One moment…" : registering ? "Create your account" : "Sign in"}<span>→</span></button>
          </form>
          {!registering && <p className="auth-switch"><Link href="/forgot-password">Forgot your password?</Link></p>}
          <p className="auth-switch">{registering ? "Already have an account?" : "New to Letterwise?"}{" "}
            <button type="button" onClick={() => { setRegistering(!registering); setError(""); setNotice(""); }}>
              {registering ? "Sign in" : "Create an account"}
            </button>
          </p>
          <p className="auth-privacy">Your emails stay yours. Always.</p>
        </div>
      </section>
    </main>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { supabase } from "@/lib/supabase";

export default function GmailCallbackPage() {
  const [message, setMessage] = useState("Connecting your Google account…");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function finish() {
      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");
      const state = params.get("state");
      const providerError = params.get("error");
      try {
        if (providerError) throw new Error("Google declined the account connection.");
        if (!code || !state) throw new Error("The Google callback is missing its authorization details.");
        const { data } = await supabase().auth.getSession();
        if (!data.session) throw new Error("Your session expired during Gmail authorization. Sign in and try again.");
        await api("/integrations/gmail/exchange", {
          method: "POST",
          body: JSON.stringify({ code, state }),
        });
        if (!cancelled) setMessage("Gmail is connected. You can close this page and return to your inbox.");
      } catch (error) {
        if (!cancelled) {
          setFailed(true);
          setMessage(error instanceof Error ? error.message : "Could not finish Gmail connection.");
        }
      }
    }
    void finish();
    return () => { cancelled = true; };
  }, []);

  return <main className="callback-page"><div className="callback-card">
    <span className={`callback-mark ${failed ? "callback-mark-error" : ""}`}>{failed ? "!" : "✳"}</span>
    <h1>{failed ? "Connection didn’t finish" : "Almost there."}</h1>
    <p>{message}</p>
    <Link className="button button-primary" href="/dashboard?view=settings">Return to Letterwise <span>→</span></Link>
  </div></main>;
}

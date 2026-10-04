"use client";

import { supabase } from "@/lib/supabase";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data, error } = await supabase().auth.getSession();
  if (error) throw new Error(error.message);
  const headers = new Headers(init.headers);
  if (data.session?.access_token) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    const result = await response.json().catch(() => null) as { detail?: unknown; message?: string } | null;
    let detail: string | undefined;
    if (typeof result?.detail === "string") {
      detail = result.detail;
    } else if (Array.isArray(result?.detail)) {
      detail = result.detail.map((issue: unknown) => {
        if (!issue || typeof issue !== "object") return "";
        const item = issue as { loc?: unknown[]; msg?: unknown };
        const location = Array.isArray(item.loc)
          ? item.loc.filter((part) => typeof part === "string" || typeof part === "number").join(".")
          : "";
        return [location, typeof item.msg === "string" ? item.msg : ""].filter(Boolean).join(": ");
      }).filter(Boolean).join("; ");
    } else if (result?.detail && typeof result.detail === "object") {
      const providerDetail = result.detail as {
        message?: unknown;
        provider_status?: unknown;
        error?: unknown;
      };
      if (typeof providerDetail.message === "string") {
        const status = typeof providerDetail.provider_status === "number"
          ? ` (provider HTTP ${providerDetail.provider_status})`
          : "";
        detail = `${providerDetail.message}${status}`;
      }
      if (providerDetail.error && typeof providerDetail.error === "object") {
        const databaseError = providerDetail.error as {
          code?: unknown;
          message?: unknown;
          details?: unknown;
          hint?: unknown;
        };
        const code = typeof databaseError.code === "string" ? ` [${databaseError.code}]` : "";
        const message = typeof databaseError.message === "string" ? databaseError.message : "";
        const details = typeof databaseError.details === "string" ? databaseError.details : "";
        const hint = typeof databaseError.hint === "string" ? databaseError.hint : "";
        const explanation = [message, details, hint && `Hint: ${hint}`].filter(Boolean).join(" ");
        if (explanation) detail = `${detail || "Database request failed"}${code}: ${explanation}`;
      }
    }
    detail ||= result?.message;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export { API_BASE };

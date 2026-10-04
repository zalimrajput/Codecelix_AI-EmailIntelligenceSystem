"use client";

import Link from "next/link";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { supabase } from "@/lib/supabase";

type View = "inbox" | "assistant" | "tasks" | "analytics" | "notifications" | "settings" | "admin";
type Email = {
  id: string; subject?: string; body_text?: string; sender_email?: string; sender_name?: string;
  preview?: string; email_date?: string; status?: "UNREAD" | "READ" | "ARCHIVED" | "DELETED"; is_starred?: boolean;
  reply_required?: boolean; action_required?: boolean; category_id?: string;
};
type Row = Record<string, unknown> & { id?: string; title?: string; status?: string };
type Overview = {
  users?: number;
  active_users?: number;
  emails?: number;
  failed_processing_runs?: number;
  ai_tokens?: number;
  total_emails?: number;
  unread_emails?: number;
  starred_emails?: number;
  reply_required?: number;
  action_required?: number;
  emails_this_month?: number;
  open_action_items?: number;
  upcoming_deadlines?: number;
  ai_tokens_used?: number;
  priorities?: Record<string, number>;
  sentiments?: Record<string, number>;
  sources?: Record<string, number>;
};
type UserDetails = {
  full_name?: string | null;
  roles: string[];
  account_status: string;
  email_verified: boolean;
  created_at?: string | null;
  email_activity: {
    emails_received: number;
    gmail_emails_received: number;
    emails_processed: number;
    unread_emails: number;
    ai_analyses: number;
    pending_action_items: number;
  };
  ai_usage: { requests: number; tokens: number };
  smart_replies_generated: number;
  last_gmail_sync_at?: string | null;
};

const nav: { id: View; label: string; icon: string }[] = [
  { id: "inbox", label: "Inbox", icon: "▣" },
  { id: "assistant", label: "AI Assistant", icon: "✳" },
  { id: "tasks", label: "Action items", icon: "☷" },
  { id: "analytics", label: "Analytics", icon: "◔" },
  { id: "notifications", label: "Notifications", icon: "♧" },
];

export default function DashboardPage() {
  const router = useRouter();
  const [view, setView] = useState<View>("inbox");
  const [emails, setEmails] = useState<Email[]>([]);
  const [selected, setSelected] = useState<Email | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [overview, setOverview] = useState<Overview>({});
  const [actions, setActions] = useState<Row[]>([]);
  const [deadlines, setDeadlines] = useState<Row[]>([]);
  const [notifications, setNotifications] = useState<Row[]>([]);
  const [conversations, setConversations] = useState<Row[]>([]);
  const [gmail, setGmail] = useState<Record<string, unknown>>({});
  const [profile, setProfile] = useState<Record<string, unknown>>({});
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const [displayName, setDisplayName] = useState("there");
  const [accountEmail, setAccountEmail] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [compose, setCompose] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [inboxTab, setInboxTab] = useState("All mail");
  const [assistantReply, setAssistantReply] = useState("");
  const [preferences, setPreferences] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (view === "inbox") {
        const params = new URLSearchParams({ page_size: "50" });
        if (query.trim()) params.set("q", query.trim());
        if (filter) params.set("status", filter);
        if (inboxTab === "Starred") params.set("starred", "true");
        if (inboxTab === "Needs reply") params.set("reply_required", "true");
        const result = await api<{ items: Email[] }>(`/emails?${params}`);
        setEmails(result.items);
      } else if (view === "analytics") {
        setOverview(await api<Overview>("/analytics/overview"));
      } else if (view === "tasks") {
        const [taskRows, deadlineRows] = await Promise.all([
          api<Row[]>("/action-items"), api<Row[]>("/deadlines"),
        ]);
        setActions(taskRows);
        setDeadlines(deadlineRows);
      } else if (view === "assistant") {
        setConversations(await api<Row[]>("/assistant/conversations"));
      } else if (view === "notifications") {
        setNotifications(await api<Row[]>("/notifications"));
      } else if (view === "settings") {
        const [gmailStatus, userProfile, prefs] = await Promise.all([
          api<Record<string, unknown>>("/integrations/gmail/status"),
          api<Record<string, unknown>>("/profile"),
          api<Record<string, boolean>>("/notification-preferences"),
        ]);
        setGmail(gmailStatus);
        setProfile(userProfile);
        setPreferences(prefs);
      } else if (view === "admin") {
        setOverview(await api<Overview>("/admin/overview"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this page.");
    } finally {
      setLoading(false);
    }
  }, [filter, inboxTab, query, view]);

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      try {
        const { data, error: sessionError } = await supabase().auth.getSession();
        if (cancelled) return;
        if (sessionError || !data.session) {
          router.replace("/login");
          return;
        }
        const identity = await api<{ roles?: string[] }>("/auth/me");
        if (cancelled) return;
        const admin = identity.roles?.includes("ADMIN") === true;
        setIsAdmin(admin);
        const first = data.session.user.user_metadata?.full_name?.split(/\s+/)[0];
        setDisplayName(first || data.session.user.email?.split("@")[0] || "there");
        setAccountEmail(data.session.user.email || "");

        const requested = new URLSearchParams(window.location.search).get("view");
        if (requested === "admin" && !admin) {
          setError("This account does not have administrator access.");
          router.replace("/dashboard");
        } else if (
          requested &&
          (nav.some((item) => item.id === requested) || requested === "settings" || requested === "admin")
        ) {
          setView(requested as View);
        }
        setAuthReady(true);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Authentication is not configured.");
          setLoading(false);
        }
      }
    }
    void initialize();
    return () => { cancelled = true; };
  }, [router]);

  useEffect(() => {
    if (authReady) void load();
  }, [authReady, load]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    void api<Record<string, unknown>>(`/emails/${selected.id}`)
      .then((result) => { if (!cancelled) setDetail(result); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : "Could not load email."); });
    return () => { cancelled = true; };
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    void api<Row[]>("/notifications")
      .then((result) => { if (!cancelled) setNotifications(result); })
      .catch(() => { if (!cancelled) setNotifications([]); });
    return () => { cancelled = true; };
  }, []);

  function changeView(next: View) {
    setView(next);
    setMobileNav(false);
    router.replace(`/dashboard?view=${next}`);
  }

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    let revokeFailed = false;
    try {
      await api("/auth/logout?scope=local", { method: "POST" });
    } catch {
      revokeFailed = true;
    }
    try {
      const { error: signOutError } = await supabase().auth.signOut({ scope: "local" });
      if (signOutError) throw signOutError;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear this device’s session.");
      setLoggingOut(false);
      return;
    }
    router.replace(revokeFailed ? "/login?logout=local" : "/login?logout=complete");
  }

  async function openEmail(email: Email) {
    setSelected(email);
    if (email.status === "UNREAD") {
      try {
        const updated = await api<Email>(`/emails/${email.id}`, {
          method: "PATCH", body: JSON.stringify({ status: "READ" }),
        });
        setEmails((items) => items.map((item) => item.id === email.id ? { ...item, ...updated } : item));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not mark email as read.");
      }
    }
  }async function connectGmail() {
    try {
      const result = await api<{ authorization_url: string }>(
        "/integrations/gmail/authorize", { method: "POST" });
      window.location.assign(result.authorization_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start Gmail connection.");
    }
  }

  const unreadCount = useMemo(() => emails.filter((email) => email.status === "UNREAD").length, [emails]);

  return (
    <main className="app-shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
        <Link href="/dashboard" className="brand"><span className="brand-mark">l.</span> letterwise</Link>
        <button className="compose-button" onClick={() => setCompose(true)}><span>＋</span> Compose email</button>
        <span className="sidebar-label">WORKSPACE</span>
        <nav className="main-nav" aria-label="Main navigation">
          {nav.map((item) => <button key={item.id} className={`nav-link ${view === item.id ? "nav-active" : ""}`} onClick={() => changeView(item.id)}>
            <span className="nav-icon">{item.icon}</span><span>{item.label}</span>
            {item.id === "inbox" && unreadCount > 0 && <span className="nav-count">{unreadCount}</span>}
          </button>)}
        </nav>
        <div className="sidebar-section">
          <span className="sidebar-label">YOUR INBOX</span>
          <button className={`nav-link ${view === "inbox" && inboxTab === "All mail" ? "nav-sub-active" : ""}`} onClick={() => { setInboxTab("All mail"); changeView("inbox"); }}><span className="nav-icon">⌑</span> All mail</button>
          <button className={`nav-link ${inboxTab === "Starred" ? "nav-sub-active" : ""}`} onClick={() => { setInboxTab("Starred"); changeView("inbox"); }}><span className="nav-icon">☆</span> Starred</button>
          <button className={`nav-link ${inboxTab === "Needs reply" ? "nav-sub-active" : ""}`} onClick={() => { setInboxTab("Needs reply"); changeView("inbox"); }}><span className="nav-icon">↗</span> Needs reply</button>
        </div>
        <div className="sidebar-bottom">
          <div className="privacy-card"><span className="privacy-icon">✳</span><div><strong>Your data, your domain.</strong><p>Your inbox is private by design.</p></div></div>
          <button className={`nav-link ${view === "settings" ? "nav-active" : ""}`} onClick={() => changeView("settings")}><span className="nav-icon">⚙</span> Settings</button>
          {isAdmin && <button className={`nav-link ${view === "admin" ? "nav-active" : ""}`} onClick={() => changeView("admin")}><span className="nav-icon">◈</span> Admin</button>}
          <button className="profile-button" onClick={() => void logout()} disabled={loggingOut} aria-label="Sign out">
            <span className="avatar">{displayName[0]?.toUpperCase()}</span>
            <span className="profile-copy"><strong>{loggingOut ? "Signing out…" : displayName}</strong><small>{isAdmin ? "Administrator" : accountEmail || "Personal workspace"}</small></span>
            <span className="profile-menu">{loggingOut ? "…" : "↗"}</span>
          </button>
        </div>
      </aside>
      {mobileNav && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
      <section className="main-area">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileNav(!mobileNav)} aria-label="Toggle menu">☰</button>
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-sep">/</span><strong>{viewTitle(view)}</strong></div>
          <label className="global-search"><span>⌕</span><input maxLength={200} value={query} onChange={(event) => { setQuery(event.target.value); if (view !== "inbox") changeView("inbox"); }} placeholder="Search your inbox..." /><kbd>⌘ K</kbd></label>
          <button className="icon-button notification-trigger" aria-label="Notifications" onClick={() => changeView("notifications")}><span>♧</span>{notifications.some((item) => !item.is_read) && <i />}</button>
          <span className="top-avatar">{displayName[0]?.toUpperCase()}</span>
        </header>
        <div className="page-content">
          {error && <div className="alert alert-error page-alert" role="alert">{error}<button aria-label="Dismiss" onClick={() => setError("")}>×</button></div>}
          {notice && <div className="alert alert-success page-alert" role="status">{notice}<button aria-label="Dismiss" onClick={() => setNotice("")}>×</button></div>}
          {view === "inbox" && <InboxView
            emails={emails} selected={selected} detail={detail} query={query} filter={filter}
            inboxTab={inboxTab} loading={loading} setFilter={setFilter} openEmail={openEmail}
            setInboxTab={setInboxTab} setCompose={setCompose} onReload={() => void load()} setError={setError}
            setSelected={setSelected} setNotice={setNotice}
          />}
          {view === "assistant" && <AssistantView
            reply={assistantReply} setReply={setAssistantReply}
            conversations={conversations} setError={setError}
          />}
          {view === "tasks" && <TasksView actions={actions} deadlines={deadlines} reload={() => void load()} setError={setError} />}
          {view === "notifications" && <NotificationsView items={notifications} setItems={setNotifications} setError={setError} />}
          {view === "analytics" && <AnalyticsView data={overview} />}
          {view === "settings" && <SettingsView gmail={gmail} profile={profile} accountEmail={accountEmail} prefs={preferences} setPrefs={setPreferences} setError={setError} reload={() => void load()} connectGmail={connectGmail} />}
          {view === "admin" && <AdminView data={overview} loading={loading} />}
        </div>
      </section>
      {compose && <ComposeModal
        onClose={() => setCompose(false)}
        onCreated={(summary) => {
          setCompose(false);
          setSelected(null);
          setError("");
          setNotice(summary);
          void load();
        }}
        setError={setError}
      />}
    </main>
  );
}

function InboxView(props: {
  emails: Email[]; selected: Email | null; detail: Record<string, unknown> | null; query: string;
  filter: string; inboxTab: string; loading: boolean; setFilter: (filter: string) => void;
  openEmail: (email: Email) => void; setInboxTab: (tab: string) => void;
  setCompose: (open: boolean) => void; onReload: () => void; setSelected: (email: Email | null) => void;
  setError: (error: string) => void; setNotice: (notice: string) => void;
}) {
  const { emails, selected, detail, query, filter, inboxTab, loading, setFilter, openEmail, setInboxTab, setCompose, onReload, setSelected, setError, setNotice } = props;
  const detailEmail = detail?.email as Email | undefined;
  const selectedEmail = detailEmail?.id === selected?.id ? detailEmail : selected;
  const activeDetail = detailEmail?.id === selected?.id ? detail : null;
  const analysis = (activeDetail?.ai_analyses as Row[] | undefined)?.[0];
  const actions = (activeDetail?.action_items as Row[] | undefined) ?? [];
  const deadlines = (activeDetail?.deadlines as Row[] | undefined) ?? [];
  const threadEmails = (activeDetail?.thread_emails as Email[] | undefined) ?? [];
  const [replyDraft, setReplyDraft] = useState<Row | null>(null);
  const [replyText, setReplyText] = useState("");
  const [draftingReply, setDraftingReply] = useState(false);
  const [sendingReply, setSendingReply] = useState(false);
  async function star(email: Email) {
    try {
      await api(`/emails/${email.id}`, { method: "PATCH", body: JSON.stringify({ is_starred: !email.is_starred }) });
      onReload();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update this email."); }
  }
  async function createReply(emailId: string) {
    if (draftingReply) return;
    setDraftingReply(true);
    try {
      const result = await api<Row>(`/emails/${emailId}/replies`, {
        method: "POST", body: JSON.stringify({ tone: "PROFESSIONAL" }),
      });
      setReplyDraft(result);
      setReplyText(String(result.generated_reply || ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not prepare this reply.");
    } finally {
      setDraftingReply(false);
    }
  }
  async function updateDraft(action: "approve" | "reject" | "save") {
    if (!replyDraft?.id) return;
    try {
      let current = replyDraft;
      if (action === "save" || (action === "approve" && replyText !== (replyDraft.edited_reply || replyDraft.generated_reply))) {
        current = await api<Row>(`/replies/${replyDraft.id}`, {
          method: "PATCH", body: JSON.stringify({ edited_reply: replyText }),
        });
        setReplyDraft(current);
      }
      if (action !== "save") {
        current = await api<Row>(`/replies/${replyDraft.id}/${action}`, { method: "POST" });
        setReplyDraft(current);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update this reply draft.");
    }
  }
  async function sendReply() {
    if (!replyDraft?.id || replyDraft.status !== "APPROVED" || sendingReply) return;
    setSendingReply(true);
    setError("");
    try {
      const sent = await api<Row>(`/integrations/gmail/replies/${replyDraft.id}/send`, { method: "POST" });
      setReplyDraft(sent);
      setNotice(`Reply sent to ${selectedEmail?.sender_email || "the original sender"}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send this reply.");
    } finally {
      setSendingReply(false);
    }
  }

  return <div className={`inbox-layout ${selected ? "has-selection" : ""}`}>
    <section className="inbox-pane">
      <div className="page-heading inbox-heading">
      <div><span className="eyebrow">{new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }).toUpperCase()}</span><h1>Your inbox, in focus.</h1><p>A little less noise. A little more know-how.</p></div>
        <button className="button button-primary" onClick={() => setCompose(true)}><span>＋</span> Compose</button>
      </div>
      <div className="inbox-summary">
        <div><span className="summary-dot" /><strong>{emails.filter((email) => email.status === "UNREAD").length}</strong><span>unread</span></div>
        <span className="summary-divider" />
        <div><strong>{emails.filter((email) => email.reply_required).length}</strong><span>need a reply</span></div>
        <span className="summary-divider" />
        <div><strong>{emails.filter((email) => email.action_required).length}</strong><span>with an action</span></div>
        <button className="refresh-button" onClick={onReload} aria-label="Refresh inbox">↻</button>
      </div>
      <div className="mail-toolbar">
        <div className="mail-tabs">{["All mail", "Starred", "Needs reply"].map((tab) => <button key={tab} onClick={() => setInboxTab(tab)} className={inboxTab === tab ? "mail-tab-active" : ""}>{tab}</button>)}</div>
        <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter email status">
          <option value="">Any status</option><option value="UNREAD">Unread</option><option value="READ">Read</option><option value="ARCHIVED">Archived</option>
        </select>
      </div>
      <div className="email-list">
        {loading ? <LoadingRows /> : emails.length ? emails.map((email) => <article key={email.id} className={`email-row ${email.status === "UNREAD" ? "email-unread" : ""} ${selected?.id === email.id ? "email-selected" : ""}`}>
          <button className="email-row-main" onClick={() => openEmail(email)}>
            <span className="email-avatar" style={{ background: avatarColor(email.sender_email) }}>{initials(email.sender_name || email.sender_email)}</span>
            <span className="email-copy"><span className="email-meta"><strong>{email.sender_name || email.sender_email || "Unknown sender"}</strong><time>{formatDate(email.email_date)}</time></span><span className="email-subject">{email.subject || "(no subject)"}</span><span className="email-preview">{email.preview || email.body_text?.slice(0, 110) || "No preview available"}</span></span>
            <span className="email-flags">{email.reply_required && <span className="tag tag-purple">Reply</span>}{email.action_required && <span className="tag tag-gold">Action</span>}</span>
          </button>
          <button className={`star-button ${email.is_starred ? "starred" : ""}`} onClick={() => void star(email)} aria-label={email.is_starred ? "Unstar email" : "Star email"}>{email.is_starred ? "★" : "☆"}</button>
        </article>) : <div className="empty-state"><span>✳</span><h3>{query ? "No matches just yet." : "A little breathing room."}</h3><p>{query ? "Try another name, subject, or phrase." : "New mail will find its way here."}</p><button onClick={() => setCompose(true)} className="button button-quiet">Write an email <span>→</span></button></div>}
      </div>
    </section>
    {selectedEmail && <aside className="detail-pane">
      <div className="detail-toolbar"><button className="back-to-inbox" onClick={() => setSelected(null)}>← <span>Inbox</span></button><div><button className="icon-button" aria-label="Archive email" onClick={() => void api(`/emails/${selectedEmail.id}`, { method: "PATCH", body: JSON.stringify({ is_archived: true, status: "ARCHIVED" }) }).then(() => { setSelected(null); onReload(); }).catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not archive email."))}>⌑</button><button className="icon-button" aria-label="Close email" onClick={() => setSelected(null)}>×</button></div></div>
      <div className="detail-content">
        <div className="detail-subject">{selectedEmail.subject || "(no subject)"}</div>
        <div className="detail-sender"><span className="email-avatar" style={{ background: avatarColor(selectedEmail.sender_email) }}>{initials(selectedEmail.sender_name || selectedEmail.sender_email)}</span><div><strong>{selectedEmail.sender_name || selectedEmail.sender_email}</strong><span>{selectedEmail.sender_email}</span></div><time>{formatDate(selectedEmail.email_date)}</time></div>
        <div className="detail-body">{selectedEmail.body_text || "This message has no plain-text body."}</div>
        {threadEmails.length > 1 && <details className="thread-history"><summary>Conversation · {threadEmails.length} messages</summary>{threadEmails.filter((item) => item.id !== selectedEmail.id).map((item) => <article key={item.id}><strong>{item.sender_name || item.sender_email || "Unknown sender"}</strong><time>{formatDate(item.email_date)}</time><p>{item.body_text || "No message body."}</p></article>)}</details>}
        <button className="button button-primary reply-button" onClick={() => void createReply(selectedEmail.id)} disabled={draftingReply}>{draftingReply ? "Drafting reply…" : "✳ Draft a thoughtful reply"}</button>
        {replyDraft && <section className="reply-draft"><div className="reply-draft-title"><span>✳</span><div><strong>Your reply draft</strong><small>{String(replyDraft.status || "DRAFT")} · {replyDraft.status === "SENT" ? "Delivered through Gmail." : "Approval does not send automatically."}</small></div></div>        <textarea value={replyText} onChange={(event) => setReplyText(event.target.value)} rows={5} disabled={replyDraft.status === "APPROVED" || replyDraft.status === "REJECTED" || replyDraft.status === "SENDING" || replyDraft.status === "SENT"} /><div className="reply-draft-actions">{replyDraft.status === "DRAFT" || replyDraft.status === "EDITED" ? <><button className="text-button" onClick={() => void updateDraft("reject")}>Reject</button><button className="button button-quiet" onClick={() => void updateDraft("save")} disabled={!replyText.trim()}>Save edits</button><button className="button button-primary" onClick={() => void updateDraft("approve")} disabled={!replyText.trim()}>Approve draft</button></> : replyDraft.status === "APPROVED" ? <><span className="reply-status-note">Approved and ready to send.</span><button className="button button-primary" onClick={() => void sendReply()} disabled={sendingReply}>{sendingReply ? "Sending…" : "Send reply"}</button></> : <span className="reply-status-note">{replyDraft.status === "SENDING" ? "Send result is being verified. Check Gmail Sent mail before retrying." : `This draft has been ${String(replyDraft.status).toLowerCase()}.`}</span>}</div></section>}
        {analysis && <section className="insight-card"><div className="insight-heading"><span>✳</span><div><strong>A little context</strong><small>AI email intelligence</small></div><span className={`priority priority-${String(analysis.priority || "medium").toLowerCase()}`}>{String(analysis.priority || "MEDIUM")}</span></div>
          {Boolean(analysis.short_summary) && <p className="analysis-summary">{String(analysis.short_summary)}</p>}
          <div className="insight-tags"><span>{String(analysis.sentiment || "NEUTRAL").toLowerCase()} tone</span>{Boolean(analysis.intent) && <span>{String(analysis.intent)}</span>}</div>
          {Boolean(analysis.detailed_summary) && <details><summary>Read the full summary</summary><p>{String(analysis.detailed_summary)}</p></details>}
          {actions.length > 0 && <div className="insight-list"><strong>Action items</strong>{actions.map((item) => <p key={String(item.id)}>· {String(item.title)}</p>)}</div>}
          {deadlines.length > 0 && <div className="insight-list"><strong>Dates to keep in mind</strong>{deadlines.map((item) => <p key={String(item.id)}>· {String(item.title)} — {formatDate(String(item.deadline_at))}</p>)}</div>}
        </section>}
        {!analysis && <section className="insight-card insight-pending"><span>✳</span><p>AI insights are on their way. They’ll appear here once this email has been processed.</p></section>}
      </div>
    </aside>}
  </div>;
}

function AssistantView(props: {
  reply: string; setReply: (value: string) => void;
  conversations: Row[]; setError: (error: string) => void;
}) {
  const { reply, setReply, conversations, setError } = props;
  const [conversationId, setConversationId] = useState("");
  const [mode, setMode] = useState<"ask" | "search">("ask");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [references, setReferences] = useState<Row[]>([]);
  const [searchResults, setSearchResults] = useState<Row[]>([]);
  const [searchState, setSearchState] = useState<"idle" | "loading" | "success" | "empty" | "error">("idle");
  const [searchError, setSearchError] = useState("");
  async function ask(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api<{ conversation_id: string; message: { content?: string }; references: Row[] }>("/assistant/ask", {
        method: "POST", body: JSON.stringify({ message, conversation_id: conversationId || null }),
      });
      setConversationId(response.conversation_id);
      setReply(String(response.message.content || ""));
      setReferences(response.references);
      setSearchResults([]);
      setSearchState("idle");
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The assistant could not answer.");
    } finally {
      setBusy(false);
    }
  }
  async function search(event: FormEvent) {
    event.preventDefault();
    const searchText = input.trim();
    if (searchText.length < 2 || busy) return;
    setBusy(true);
    setError("");
    setSearchError("");
    setSearchResults([]);
    setSearchState("loading");
    try {
      const matches = await api<Row[]>(`/assistant/search?q=${encodeURIComponent(searchText)}`);
      setSearchResults(matches);
      setSearchState(matches.length ? "success" : "empty");
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "Semantic search is unavailable.");
      setSearchState("error");
    } finally {
      setBusy(false);
    }
  }
  function submit(event: FormEvent) {
    if (mode === "ask") void ask(event);
    else void search(event);
  }
  function changeMode(nextMode: "ask" | "search") {
    setMode(nextMode);
    setSearchError("");
    setSearchResults([]);
    setSearchState("idle");
  }
  return <div className="content-narrow">
    <div className="page-heading"><span className="eyebrow">YOUR PERSONAL RESEARCHER</span><h1>Ask your inbox.</h1><p>Ask a question or find an email, all in one place.</p></div>
    <section className="assistant-card"><div className="assistant-card-head"><div className="assistant-emblem">✳</div><div><strong>A clearer way to find an answer.</strong><p>{mode === "ask" ? "Get a grounded answer from your emails." : "Find emails by the meaning you remember."}</p></div></div>
      <div className="assistant-mode-switch" role="group" aria-label="Choose assistant mode">
        <button type="button" className={mode === "ask" ? "assistant-mode-active" : ""} aria-pressed={mode === "ask"} onClick={() => changeMode("ask")}>Ask</button>
        <button type="button" className={mode === "search" ? "assistant-mode-active" : ""} aria-pressed={mode === "search"} onClick={() => changeMode("search")}>Find emails</button>
      </div>
      {mode === "ask" && reply && <div className="assistant-response"><span className="eyebrow">HERE’S WHAT I FOUND</span><p>{reply}</p></div>}
      <form className="assistant-form" onSubmit={submit}>
        <textarea maxLength={mode === "ask" ? 4000 : 2000} value={input} onChange={(event) => { setInput(event.target.value); setSearchResults([]); setSearchError(""); setSearchState("idle"); }} placeholder={mode === "ask" ? "Ask something about your emails…" : "Find emails about what you remember…"} rows={3} />
        <div className="assistant-form-footer"><span>{mode === "ask" ? "Answers stay grounded in your own email." : "Searches your indexed email and shows the closest matches."}</span><button className="button button-primary" disabled={busy || (mode === "ask" ? !input.trim() : input.trim().length < 2)}>{busy ? (mode === "ask" ? "Thinking…" : "Searching…") : (mode === "ask" ? "Ask assistant" : "Find emails")} <span>→</span></button></div>
      </form>
      {mode === "ask" && references.length > 0 && <div className="reference-list"><span className="eyebrow">EMAILS THAT INFORMED THIS ANSWER</span>{references.map((item, i) => <div className="reference-row" key={String(item.chunk_id || i)}><span>↗</span><div><strong>{String(item.subject || "Email match")}</strong><p>{String(item.content || "").slice(0, 220)}</p></div><small>{Math.round(Number(item.similarity || 0) * 100)}%</small></div>)}</div>}
      {mode === "search" && searchState === "loading" && <p className="semantic-search-status" role="status">Searching your indexed emails…</p>}
      {mode === "search" && searchState === "error" && <p className="semantic-search-status semantic-search-error" role="alert">{searchError}</p>}
      {mode === "search" && searchState === "empty" && <p className="semantic-search-status" role="status">No closely matching emails were found. Try different or more specific search words.</p>}
      {mode === "search" && searchState === "success" && <section className="semantic-search-results" aria-live="polite"><span className="eyebrow">{searchResults.length} MATCHING {searchResults.length === 1 ? "EMAIL" : "EMAILS"}</span>{searchResults.map((item, i) => <article className="semantic-result" key={String(item.chunk_id || i)}><div><strong>{String(item.subject || "Email match")}</strong>{Boolean(item.sender_email) && <small>{String(item.sender_email)}</small>}<p>{String(item.content || "").slice(0, 500)}</p></div><span>{Math.round(Number(item.similarity || 0) * 100)}%</span></article>)}</section>}
      {mode === "ask" && <div className="conversation-note"><span>↺</span> You have {conversations.length} saved {conversations.length === 1 ? "conversation" : "conversations"} in your workspace.</div>}
    </section>
  </div>;
}

function TasksView({ actions, deadlines, reload, setError }: { actions: Row[]; deadlines: Row[]; reload: () => void; setError: (value: string) => void }) {
  const open = actions.filter((item) => item.status !== "COMPLETED" && item.status !== "CANCELLED");
  async function toggleAction(item: Row) {
    try {
      await api(`/action-items/${item.id}`, {
        method: "PATCH", body: JSON.stringify({ status: item.status === "COMPLETED" ? "PENDING" : "COMPLETED" }),
      });
      reload();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update action item."); }
  }
  async function toggleDeadline(item: Row) {
    try {
      await api(`/deadlines/${item.id}`, {
        method: "PATCH", body: JSON.stringify({ is_completed: !item.is_completed }),
      });
      reload();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update deadline."); }
  }
  return <div className="content-narrow"><div className="page-heading"><span className="eyebrow">SMALL STEPS, CLEARLY SEEN</span><h1>Things worth following up.</h1><p>Action items and deadlines, brought together from your inbox.</p></div>
    <div className="task-overview"><span className="task-number">{open.length}</span><div><strong>still on your plate</strong><p>You’ve got this. One thing at a time.</p></div><span className="task-spark">✳</span></div>
    <section className="list-card"><header><div><span className="eyebrow">YOUR FOLLOW-UPS</span><h2>Action items</h2></div><span className="count-pill">{actions.length}</span></header>
      {actions.length ? actions.map((item) => <div className={`task-row ${item.status === "COMPLETED" ? "task-done" : ""}`} key={String(item.id)}><button className="check-button" onClick={() => void toggleAction(item)} aria-label="Toggle action item">{item.status === "COMPLETED" ? "✓" : ""}</button><div><strong>{String(item.title || "Untitled action")}</strong><p>{String(item.description || "From an email in your inbox")}</p></div><time>{item.due_date ? formatDate(String(item.due_date)) : ""}</time></div>) : <EmptyList text="Nothing to follow up on yet. Action items from your emails will appear here." />}
    </section>
    <section className="list-card deadlines-card"><header><div><span className="eyebrow">KEEP AN EYE ON</span><h2>Upcoming deadlines</h2></div><span className="count-pill">{deadlines.filter((item) => !item.is_completed).length}</span></header>
      {deadlines.length ? deadlines.map((item) => <div className={`task-row ${item.is_completed ? "task-done" : ""}`} key={String(item.id)}><button className="check-button" onClick={() => void toggleDeadline(item)} aria-label="Toggle deadline">{item.is_completed ? "✓" : ""}</button><div><strong>{String(item.title || "Untitled deadline")}</strong><p>{String(item.description || item.original_text || "From an email in your inbox")}</p></div><time>{formatDate(String(item.deadline_at || ""))}</time></div>) : <EmptyList text="No dates to keep track of right now." />}
    </section>
  </div>;
}

function NotificationsView({ items, setItems, setError }: {
  items: Row[]; setItems: (items: Row[]) => void; setError: (value: string) => void;
}) {
  async function markRead(item: Row) {
    try {
      const updated = await api<Row>(`/notifications/${item.id}/read`, { method: "PATCH" });
      setItems(items.map((notification) => notification.id === item.id ? { ...notification, ...updated } : notification));
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update notification."); }
  }
  return <div className="content-narrow"><div className="page-heading"><span className="eyebrow">A FEW THINGS TO KNOW</span><h1>Nothing gets lost.</h1><p>Gentle nudges about replies, dates, and things that need your attention.</p></div>
    <section className="list-card"><header><div><span className="eyebrow">YOUR UPDATES</span><h2>Notifications</h2></div><span className="count-pill">{items.filter((item) => !item.is_read).length} unread</span></header>
      {items.length ? items.map((item) => <div className={`notification-row ${item.is_read ? "" : "notification-unread"}`} key={String(item.id)}><span className="notification-icon">✳</span><div><strong>{String(item.title || "A note for you")}</strong><p>{String(item.message || "")}</p><time>{formatDate(String(item.created_at || ""))}</time></div>{!item.is_read && <button className="text-button" onClick={() => void markRead(item)}>Mark read</button>}</div>) : <EmptyList text="You’re all caught up. Anything that needs your attention will appear here." />}
    </section>
  </div>;
}

function AnalyticsView({ data }: { data: Overview }) {
  const metrics = [
    ["All mail", data.total_emails ?? 0, "Messages in your workspace", "◫"],
    ["Unread", data.unread_emails ?? 0, "Still waiting for you", "◉"],
    ["Need a reply", data.reply_required ?? 0, "A thoughtful response goes a long way", "↗"],
    ["With an action", data.action_required ?? 0, "Follow-ups surfaced by AI", "☷"],
    ["This month", data.emails_this_month ?? 0, "A fresh look at the last 30 days", "◷"],
    ["Upcoming dates", data.upcoming_deadlines ?? 0, "The next 7 days", "⌑"],
  ] as const;
  const priorities = data.priorities;
  const sentiments = data.sentiments;
  return <div className="content-wide"><div className="page-heading"><span className="eyebrow">A LITTLE PERSPECTIVE</span><h1>Your email, at a glance.</h1><p>Patterns to help you spend your attention where it counts.</p></div>
    <div className="metric-grid">{metrics.map(([label, value, caption, icon]) => <div className="metric-card" key={label}><span className="metric-icon">{icon}</span><strong className="metric-value">{value.toLocaleString()}</strong><span className="metric-label">{label}</span><small>{caption}</small></div>)}</div>
    <div className="analytics-row"><ChartCard title="Priority at a glance" subtitle="From AI analysis" entries={priorities} accent="purple" /><ChartCard title="The tone of things" subtitle="Sentiment across your email" entries={sentiments} accent="green" /></div>
    <div className="source-card"><div><span className="eyebrow">CONNECTED SPACES</span><h2>One inbox, many origins.</h2><p>Your manual, imported, and synced emails — all together.</p></div><div className="source-stats">{Object.entries(data.sources || {}).map(([key, value]) => <span key={key}><i />{key.toLowerCase()}<strong>{value}</strong></span>)}</div></div>
  </div>;
}

function ChartCard({ title, subtitle, entries, accent }: { title: string; subtitle: string; entries?: Record<string, number>; accent: "purple" | "green" }) {
  const rows = Object.entries(entries || {});
  const max = Math.max(1, ...rows.map(([, value]) => value));
  return <section className="chart-card"><div><span className="eyebrow">{subtitle}</span><h2>{title}</h2></div>
    {rows.length ? (
      <div className="bar-list">{rows.map(([label, value]) => <div className="bar-row" key={label}><div className="bar-label"><span>{label.toLowerCase()}</span><strong>{value}</strong></div><div className={`bar-track bar-${accent}`}><i style={{ width: `${Math.max(3, (value / max) * 100)}%` }} /></div></div>)}</div>
    ) : <EmptyList text="Once there are some analyzed emails, your patterns will show up here." />}
  </section>;
}

function SettingsView(props: {
  gmail: Record<string, unknown>; profile: Record<string, unknown>; accountEmail: string; prefs: Record<string, boolean>;
  setPrefs: (value: Record<string, boolean>) => void; setError: (value: string) => void;
  reload: () => void; connectGmail: () => void;
}) {
  const { gmail, profile, accountEmail, prefs, setPrefs, setError, reload, connectGmail } = props;
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  async function sync() {
    setSyncing(true);
    try {
      const result = await api<{ imported: number; reprocessed?: number; skipped?: number }>("/integrations/gmail/sync", { method: "POST" });
      setError("");
      const reprocessed = result.reprocessed || 0;
      window.alert(
        `Gmail sync started. ${result.imported} new emails and ${reprocessed} existing emails were queued for processing.`,
      );
    } catch (err) { setError(err instanceof Error ? err.message : "Could not sync Gmail."); }
    finally { setSyncing(false); }
  }
  async function disconnect() {
    try { await api("/integrations/gmail", { method: "DELETE" }); reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not disconnect Gmail."); }
  }
  async function savePreferences() {
    setSaving(true);
    const preferenceKeys = [
      "urgent_email", "customer_complaint", "reply_required", "upcoming_deadline",
      "action_item", "ai_processing_completed", "email_notifications", "in_app_notifications",
    ];
    const preferenceDefaults: Record<string, boolean> = {
      ai_processing_completed: false,
      email_notifications: false,
    };
    const values = Object.fromEntries(
      preferenceKeys.map((key) => [key, prefs[key] ?? preferenceDefaults[key] ?? true]),
    );
    try { await api("/notification-preferences", { method: "PUT", body: JSON.stringify(values) }); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not save preferences."); }
    finally { setSaving(false); }
  }
  const switches = [
    ["urgent_email", "Urgent messages", "When something needs attention now"],
    ["customer_complaint", "Customer concerns", "When a note deserves extra care"],
    ["reply_required", "Replies to write", "A gentle reminder when someone is waiting"],
    ["upcoming_deadline", "Upcoming deadlines", "Dates that are getting closer"],
    ["action_item", "New action items", "Follow-ups pulled from an email"],
    ["in_app_notifications", "In-app notifications", "Updates in your Letterwise workspace"],
  ];
  return <div className="content-narrow"><div className="page-heading"><span className="eyebrow">MAKE IT YOURS</span><h1>A few things to set up.</h1><p>Your inbox, your connections, your preferences.</p></div>
    <section className="list-card settings-card"><header><div><span className="eyebrow">CONNECTED ACCOUNTS</span><h2>Your Gmail</h2></div><span className={`connection-pill ${gmail.connected ? "connection-on" : ""}`}><i />{gmail.connected ? "Connected" : "Not connected"}</span></header>
      {gmail.connected ? <div className="gmail-connected"><span className="gmail-mark">G</span><div><strong>{String(gmail.google_email || "Google account")}</strong><p>Email sync is user-initiated. Replies are sent only when you choose Send.</p>{Boolean(gmail.last_synced_at) && <small>Last sync: {formatDate(String(gmail.last_synced_at))}</small>}</div><button className="button button-quiet" onClick={() => void connectGmail()}>Reconnect Gmail</button><button className="button button-quiet" onClick={() => void sync()} disabled={syncing}>{syncing ? "Syncing…" : "Sync now"}</button><button className="text-button" onClick={() => void disconnect()}>Disconnect</button></div> : <div className="gmail-connect"><div className="gmail-mark">G</div><div><strong>Bring your Gmail into focus.</strong><p>Connect to sync email and send replies only when you choose Send.</p></div><button className="button button-primary" onClick={connectGmail}>Connect Gmail <span>→</span></button></div>}
    </section>
    <section className="list-card settings-card"><header><div><span className="eyebrow">A FEW THINGS ABOUT YOU</span><h2>Your profile</h2></div></header><div className="profile-summary"><span className="avatar">{String(profile.full_name || "U").charAt(0).toUpperCase()}</span><div><strong>{String(profile.full_name || "Your name")}</strong><p>{accountEmail || "Your profile is stored securely."}</p></div><span className="profile-lock">Private</span></div></section>
    <section className="list-card settings-card"><header><div><span className="eyebrow">JUST THE RIGHT AMOUNT</span><h2>Notifications</h2><p>Choose what deserves a little nudge.</p></div></header>
      {switches.map(([key, title, caption]) => <label className="switch-row" key={key}><span><strong>{title}</strong><small>{caption}</small></span><input type="checkbox" checked={prefs[key] ?? true} onChange={(event) => setPrefs({ ...prefs, [key]: event.target.checked })} /></label>)}
      <button className="button button-primary settings-save" onClick={() => void savePreferences()} disabled={saving}>{saving ? "Saving…" : "Save preferences"} <span>→</span></button>
    </section>
  </div>;
}

function AdminView({ data, loading }: { data: Overview; loading: boolean }) {
  const [users, setUsers] = useState<Row[]>([]);
  const [audit, setAudit] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [selectedUser, setSelectedUser] = useState<Row | null>(null);
  const [userDetails, setUserDetails] = useState<UserDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState("");
  useEffect(() => {
    let cancelled = false;
    void Promise.all([api<Row[]>("/admin/users"), api<Row[]>("/admin/audit-logs")])
      .then(([userRows, auditRows]) => { if (!cancelled) { setUsers(userRows); setAudit(auditRows); } })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : "Admin access is unavailable."); });
    return () => { cancelled = true; };
  }, []);
  async function saveRoles(person: Row) {
    const roles = Array.isArray(person.roles) ? person.roles as string[] : [];
    const next = roles.includes("ADMIN") ? roles.filter((role) => role !== "ADMIN") : [...roles, "ADMIN"];
    if (next.length === 0) next.push("USER");
    try {
      await api(`/admin/users/${person.id}/roles`, { method: "PATCH", body: JSON.stringify({ roles: next }) });
      setUsers(users.map((item) => item.id === person.id ? { ...item, roles: next } : item));
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update this user’s roles."); }
  }
  async function setActive(person: Row) {
    try {
      const active = !person.is_active;
      await api(`/admin/users/${person.id}/active?active=${active}`, { method: "PATCH" });
      setUsers(users.map((item) => item.id === person.id ? { ...item, is_active: active } : item));
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update this account."); }
  }
  async function viewDetails(person: Row) {
    setSelectedUser(person);
    setUserDetails(null);
    setDetailsError("");
    setDetailsLoading(true);
    try {
      setUserDetails(await api<UserDetails>(`/admin/users/${person.id}`));
    } catch (err) {
      setDetailsError(err instanceof Error ? err.message : "Could not load user details.");
    } finally {
      setDetailsLoading(false);
    }
  }
  function closeDetails() {
    setSelectedUser(null);
    setUserDetails(null);
    setDetailsError("");
  }
  return <div className="content-wide"><div className="page-heading"><span className="eyebrow">A LITTLE HOUSEKEEPING</span><h1>Workspace overview.</h1><p>Health, usage, and the people who call this home.</p></div>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="metric-grid admin-metrics"><Metric label="People" value={loading ? "—" : data.users ?? 0} icon="♙" /><Metric label="Active accounts" value={loading ? "—" : data.active_users ?? 0} icon="◉" /><Metric label="Emails stored" value={loading ? "—" : data.emails ?? 0} icon="▣" /><Metric label="AI tokens used" value={loading ? "—" : (data.ai_tokens ?? 0).toLocaleString()} icon="✳" /><Metric label="Processing errors" value={loading ? "—" : data.failed_processing_runs ?? 0} icon="!" /></div>
    <div className="admin-grid"><section className="list-card"><header><div><span className="eyebrow">YOUR COMMUNITY</span><h2>People</h2></div></header>{users.length ? users.map((person) => <div className="admin-user" key={String(person.id)}><span className="avatar">{String(person.full_name || "U").charAt(0).toUpperCase()}</span><div><strong>{String(person.full_name || "Member")}</strong><small>{Array.isArray(person.roles) ? (person.roles as string[]).join(", ") : "No role"} · {person.email_verified ? "Verified" : "Pending"}</small></div><button className="text-button admin-action" onClick={() => void viewDetails(person)}>View details</button><button className="text-button admin-action" onClick={() => void saveRoles(person)}>{Array.isArray(person.roles) && (person.roles as string[]).includes("ADMIN") ? "Remove admin" : "Make admin"}</button><button className="text-button admin-action" onClick={() => void setActive(person)}>{person.is_active ? "Deactivate" : "Activate"}</button></div>) : <EmptyList text="People will appear here once they have joined." />}</section>
    <section className="list-card"><header><div><span className="eyebrow">A RECORD OF CHANGES</span><h2>Recent activity</h2></div></header>{audit.length ? audit.slice(0, 8).map((item, i) => <div className="audit-row" key={String(item.id || i)}><span>↗</span><div><strong>{String(item.action || "Workspace update")}</strong><small>{String(item.description || item.entity_type || "")}</small></div><time>{formatDate(String(item.created_at || ""))}</time></div>) : <EmptyList text="There’s no recent activity to report." />}</section></div>
    {selectedUser && <div className="modal-backdrop" onClick={closeDetails}>
      <section className="compose-modal user-details-modal" role="dialog" aria-modal="true" aria-labelledby="user-details-title" onClick={(event) => event.stopPropagation()}>
        <header><div><span className="eyebrow">PRIVATE CONTENT IS NOT SHOWN</span><h2 id="user-details-title">{String(userDetails?.full_name || selectedUser.full_name || "User details")}</h2></div><button className="icon-button" aria-label="Close user details" onClick={closeDetails}>×</button></header>
        {detailsLoading ? <div className="list-empty"><span>✳</span><p>Loading account details…</p></div> : detailsError ? <div className="alert alert-error" role="alert">{detailsError}</div> : userDetails && <>
          <div className="user-details-summary">
            <span className={`connection-pill ${userDetails.account_status === "ACTIVE" ? "connection-on" : ""}`}><i />{userDetails.account_status}</span>
            <span>{userDetails.roles.join(", ") || "No role assigned"}</span>
            <span>{userDetails.email_verified ? "Email verified" : "Email not verified"}</span>
            <span>Joined {formatDate(userDetails.created_at || "") || "Unknown"}</span>
          </div>
          <span className="eyebrow user-details-section-label">EMAIL ACTIVITY</span>
          <div className="user-details-metrics">
            <Metric label="Emails received" value={userDetails.email_activity.emails_received} icon="▣" caption="For this account" />
            <Metric label="From Gmail" value={userDetails.email_activity.gmail_emails_received} icon="G" caption="For this account" />
            <Metric label="Emails processed" value={userDetails.email_activity.emails_processed} icon="✓" caption="For this account" />
            <Metric label="Unread emails" value={userDetails.email_activity.unread_emails} icon="●" caption="For this account" />
            <Metric label="AI analyses" value={userDetails.email_activity.ai_analyses} icon="✳" caption="For this account" />
            <Metric label="Pending action items" value={userDetails.email_activity.pending_action_items} icon="☷" caption="For this account" />
          </div>
          <span className="eyebrow user-details-section-label">AI & CONNECTIONS</span>
          <div className="user-details-facts">
            <div><small>AI requests</small><strong>{userDetails.ai_usage.requests.toLocaleString()}</strong></div>
            <div><small>AI tokens used</small><strong>{userDetails.ai_usage.tokens.toLocaleString()}</strong></div>
            <div><small>Smart replies generated</small><strong>{userDetails.smart_replies_generated.toLocaleString()}</strong></div>
            <div><small>Last Gmail sync</small><strong>{formatDate(userDetails.last_gmail_sync_at || "") || "Never synced"}</strong></div>
          </div>
          <p className="user-details-privacy">This view contains account metadata and aggregate usage only. Email messages, summaries, and generated reply text are never included.</p>
        </>}
      </section>
    </div>}
  </div>;
}

function ComposeModal({ onClose, onCreated, setError }: { onClose: () => void; onCreated: (summary: string) => void; setError: (value: string) => void }) {
  const [subject, setSubject] = useState("");
  const [sender, setSender] = useState("");
  const [recipient, setRecipient] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    try {
      if (file) {
        const data = new FormData();
        data.append("file", file);
        const result = await api<{ imported: number; failed: number; errors: string[] }>("/emails/import", { method: "POST", body: data });
        const summary = result.failed
          ? `Imported ${result.imported} email${result.imported === 1 ? "" : "s"}; ${result.failed} record${result.failed === 1 ? "" : "s"} could not be imported. ${result.errors.slice(0, 3).join(" ")}`
          : `Imported ${result.imported} email${result.imported === 1 ? "" : "s"} and queued them for AI processing.`;
        onCreated(summary);
        return;
      } else {
        await api("/emails", {
          method: "POST",
          body: JSON.stringify({
            subject,
            sender_email: sender || null,
            receiver_email: recipient || null,
            body_text: body,
          }),
        });
      }
      onCreated("Email saved and queued for AI processing.");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not add this email."); }
    finally { setSending(false); }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="compose-modal" role="dialog" aria-modal="true" aria-labelledby="compose-title">
    <header><div><span className="eyebrow">MAKE IT YOUR OWN</span><h2 id="compose-title">Bring an email into focus.</h2></div><button className="icon-button" onClick={onClose} aria-label="Close">×</button></header>
    <div className="compose-tabs"><span>Write or paste email text</span><span>or</span><label className="file-attach">Upload .eml, .csv, or .json<input type="file" accept=".eml,.csv,.json" onChange={(event) => {
      const selectedFile = event.target.files?.[0] || null;
      setError("");
      setFile(selectedFile);
    }} /></label></div>
    {file && <div className="file-selected">▧ {file.name}<button onClick={() => setFile(null)}>Remove</button></div>}
    <form onSubmit={(event) => void submit(event)} className="compose-form">
      {!file && <>
        <label>From<input type="email" maxLength={254} value={sender} onChange={(event) => setSender(event.target.value)} placeholder="sender@example.com" /></label>
        <label>To<input type="email" maxLength={254} value={recipient} onChange={(event) => setRecipient(event.target.value)} placeholder="recipient@example.com" /></label>
        <label>Subject<input required maxLength={998} value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="What’s on your mind?" /></label>
        <label>Email text<textarea required value={body} onChange={(event) => setBody(event.target.value)} rows={7} placeholder="Write or paste the email body here…" /></label>
      </>}
      <div className="compose-footer"><span>✳ AI will find the useful bits automatically.</span><button className="button button-primary" disabled={sending}>{sending ? "Adding…" : file ? "Import email" : "Add to inbox"} <span>→</span></button></div>
    </form>
  </section></div>;
}

function Metric({ label, value, icon, caption = "Workspace, across all accounts" }: { label: string; value: ReactNode; icon: string; caption?: string }) {
  return <div className="metric-card"><span className="metric-icon">{icon}</span><strong className="metric-value">{value}</strong><span className="metric-label">{label}</span><small>{caption}</small></div>;
}

function EmptyList({ text }: { text: string }) {
  return <div className="list-empty"><span>✳</span><p>{text}</p></div>;
}

function LoadingRows() {
  return <div className="loading-list"><div /><div /><div /><div /></div>;
}

function viewTitle(view: View) {
  return ({ inbox: "Inbox", assistant: "AI Assistant", tasks: "Action items", analytics: "Analytics", notifications: "Notifications", settings: "Settings", admin: "Admin" })[view];
}

function initials(value?: string) {
  return (value || "?").split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join("");
}

function avatarColor(value?: string) {
  const colors = ["#e7ede4", "#f4e7da", "#e9e4f0", "#e3ebee", "#f1e8d7"];
  const code = [...(value || "a")].reduce((sum, letter) => sum + letter.charCodeAt(0), 0);
  return colors[code % colors.length];
}

function formatDate(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

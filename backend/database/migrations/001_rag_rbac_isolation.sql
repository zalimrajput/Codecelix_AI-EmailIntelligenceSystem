-- ============================================================================
-- Migration 001: RAG/embedding tables + RBAC tables + data isolation (RLS)
-- AI Email Intelligence & Smart Reply System
--
-- Adds the remaining tables from the spec:
--   * RAG layer ........ email_chunks, embeddings, rag_retrievals (pgvector)
--   * RBAC ............. roles, permissions, role_permissions, user_roles
--                        (Admin / User, assigned separately from profiles)
--   * Data isolation ... Row Level Security policies for ALL public tables.
--                         RLS was already ENABLED on the 20 existing tables
--                         but ZERO policies existed, so no API role could read
--                         or write anything. This migration adds owner-based
--                         policies for every table.
--
-- Conventions copied from the existing schema:
--   * uuid PKs default uuid_generate_v4(), timestamptz timestamps
--   * updated_at maintained by the existing update_updated_at_column() trigger
--   * ownership via user_id -> profiles.id (profiles.id -> auth.users.id)
--   * auth.uid() for the authenticated Supabase user in policies
--
-- Admin bypass: public.is_admin() is true when the user holds the ADMIN role
-- in user_roles (the single source of truth for roles since migration 003).
-- Bootstrap note: granting an admin must be done from a trusted server-side
-- connection (postgres / service_role, no JWT), e.g.
--   INSERT INTO user_roles (user_id, role_id)
--   SELECT '<uuid>', id FROM roles WHERE name = 'ADMIN';
--
-- Idempotent: safe to re-run. Apply with backend/database/run_migrations.py
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. pgvector extension (required by embeddings.embedding)
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- ----------------------------------------------------------------------------
-- 2. RAG / embedding tables
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.email_chunks (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
    email_id    uuid NOT NULL REFERENCES public.emails (id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    content     text NOT NULL,
    token_count integer,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_email_chunks_email_chunk UNIQUE (email_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS public.embeddings (
    id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
    chunk_id   uuid NOT NULL REFERENCES public.email_chunks (id) ON DELETE CASCADE,
    embedding  vector(1536) NOT NULL,
    model      varchar(100) NOT NULL DEFAULT 'text-embedding-3-small',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_embeddings_chunk_model UNIQUE (chunk_id, model)
);

CREATE TABLE IF NOT EXISTS public.rag_retrievals (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
    conversation_id uuid REFERENCES public.ai_conversations (id) ON DELETE SET NULL,
    chunk_id        uuid REFERENCES public.email_chunks (id) ON DELETE SET NULL,
    query_text      text NOT NULL,
    similarity      double precision,
    model           varchar(100),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_chunks_user_id  ON public.email_chunks (user_id);
CREATE INDEX IF NOT EXISTS idx_email_chunks_email_id ON public.email_chunks (email_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_user_id    ON public.embeddings (user_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_chunk_id   ON public.embeddings (chunk_id);
CREATE INDEX IF NOT EXISTS idx_rag_retrievals_user        ON public.rag_retrievals (user_id);
CREATE INDEX IF NOT EXISTS idx_rag_retrievals_conversation ON public.rag_retrievals (conversation_id);
CREATE INDEX IF NOT EXISTS idx_rag_retrievals_created_at  ON public.rag_retrievals (created_at DESC);

-- Vector similarity index for RAG retrieval (cosine distance).
CREATE INDEX IF NOT EXISTS idx_embeddings_vector ON public.embeddings
    USING hnsw (embedding vector_cosine_ops);

DROP TRIGGER IF EXISTS update_email_chunks_updated_at ON public.email_chunks;
CREATE TRIGGER update_email_chunks_updated_at
    BEFORE UPDATE ON public.email_chunks
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ----------------------------------------------------------------------------
-- 3. RBAC tables (spec: roles, permissions, role-permission mappings and
--    user-role assignments are maintained separately)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.roles (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        varchar(50) NOT NULL UNIQUE,
    description text,
    is_system   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.permissions (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    code        varchar(100) NOT NULL UNIQUE,
    name        varchar(150) NOT NULL,
    description text,
    resource    varchar(100) NOT NULL,
    action      varchar(50) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.role_permissions (
    role_id       uuid NOT NULL REFERENCES public.roles (id) ON DELETE CASCADE,
    permission_id uuid NOT NULL REFERENCES public.permissions (id) ON DELETE CASCADE,
    granted_by    uuid REFERENCES public.profiles (id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS public.user_roles (
    user_id     uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
    role_id     uuid NOT NULL REFERENCES public.roles (id) ON DELETE CASCADE,
    assigned_by uuid REFERENCES public.profiles (id) ON DELETE SET NULL,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_role_id         ON public.user_roles (role_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_permission ON public.role_permissions (permission_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.email_chunks, public.embeddings, public.rag_retrievals,
    public.roles, public.permissions, public.role_permissions, public.user_roles
TO authenticated;
REVOKE ALL ON TABLE
    public.email_chunks, public.embeddings, public.rag_retrievals,
    public.roles, public.permissions, public.role_permissions, public.user_roles
FROM anon;

DROP TRIGGER IF EXISTS update_roles_updated_at ON public.roles;
CREATE TRIGGER update_roles_updated_at
    BEFORE UPDATE ON public.roles
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

DROP TRIGGER IF EXISTS update_permissions_updated_at ON public.permissions;
CREATE TRIGGER update_permissions_updated_at
    BEFORE UPDATE ON public.permissions
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ---- seed roles -------------------------------------------------------------
INSERT INTO public.roles (name, description) VALUES
    ('ADMIN', 'Administrator: manages users, roles, system settings, AI usage and audit logs'),
    ('USER',  'Normal user: own emails, AI analysis, smart replies, actions, deadlines, notifications and AI assistant')
ON CONFLICT (name) DO NOTHING;

-- ---- seed permissions -------------------------------------------------------
INSERT INTO public.permissions (code, name, resource, action, description) VALUES
    -- normal user capabilities (own data)
    ('emails:read',          'Read emails',              'emails',          'read',    'Read own emails and threads'),
    ('emails:write',         'Create and update emails', 'emails',          'write',   'Import, create and update own emails'),
    ('emails:delete',        'Delete emails',            'emails',          'delete',  'Delete own emails'),
    ('ai_analysis:read',     'Read AI analysis',         'ai_analysis',     'read',    'View AI analysis, extracted information, action items and deadlines'),
    ('smart_replies:read',   'Read smart replies',       'smart_replies',   'read',    'View generated smart replies'),
    ('smart_replies:generate','Generate smart replies',  'smart_replies',   'generate','Generate smart replies for own emails'),
    ('smart_replies:approve','Approve smart replies',    'smart_replies',   'approve', 'Approve, reject or edit own smart replies'),
    ('action_items:manage',  'Manage action items',      'action_items',    'manage',  'Create and update own action items'),
    ('deadlines:manage',     'Manage deadlines',         'deadlines',       'manage',  'Create and update own deadlines'),
    ('notifications:manage', 'Manage notifications',     'notifications',   'manage',  'Read and mark own notifications'),
    ('ai_assistant:use',     'Use AI assistant',         'ai_assistant',    'use',     'Ask the AI assistant and run semantic (RAG) search over own emails'),
    ('profile:update',       'Update own profile',       'profile',         'update',  'Update own profile'),
    -- administrator capabilities
    ('users:read',           'Read users',               'users',           'read',    'View all user accounts'),
    ('users:manage',         'Manage users',             'users',           'manage',  'Create, update and deactivate users and assign roles'),
    ('roles:manage',         'Manage roles',             'roles',           'manage',  'Manage roles, permissions and role assignments'),
    ('system_settings:manage','Manage system settings',  'system_settings', 'manage',  'View and update system settings'),
    ('ai_usage:read',        'Read AI usage',            'ai_usage',        'read',    'View AI usage and cost logs for all users'),
    ('audit_logs:read',      'Read audit logs',          'audit_logs',      'read',    'View audit logs for all users')
ON CONFLICT (code) DO NOTHING;

-- ---- role -> permission mapping ---------------------------------------------
-- ADMIN gets every permission
INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
CROSS JOIN public.permissions p
WHERE r.name = 'ADMIN'
ON CONFLICT DO NOTHING;

-- USER gets the normal-user capabilities
INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.code IN (
    'emails:read', 'emails:write', 'emails:delete',
    'ai_analysis:read',
    'smart_replies:read', 'smart_replies:generate', 'smart_replies:approve',
    'action_items:manage', 'deadlines:manage', 'notifications:manage',
    'ai_assistant:use', 'profile:update'
)
WHERE r.name = 'USER'
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- 4. Helper functions
-- ----------------------------------------------------------------------------

-- Admin check used inside RLS policies. SECURITY DEFINER so it can read the
-- RBAC tables without triggering their own policies (no recursion).
-- Roles live only in user_roles (single source of truth, see migration 003).
CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.user_roles ur
        JOIN public.roles r ON r.id = ur.role_id
        WHERE ur.user_id = auth.uid() AND r.name = 'ADMIN'
    );
$$;
REVOKE ALL ON FUNCTION public.is_admin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated, service_role;

-- ----------------------------------------------------------------------------
-- 5. Data isolation: Row Level Security policies for every table.
--    All policies apply to the 'authenticated' API role; admins bypass via
--    public.is_admin(); service_role and direct postgres connections bypass RLS.
--    Bootstrap of an admin: run as postgres/service_role without a JWT, e.g.
--    INSERT INTO user_roles (user_id, role_id)
--    SELECT '<uuid>', id FROM roles WHERE name = 'ADMIN';
-- ----------------------------------------------------------------------------

-- 5a. Tables with a direct user_id ownership column ---------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'emails', 'email_threads', 'email_senders', 'email_imports',
        'notification_preferences', 'notifications', 'ai_conversations',
        'ai_replies', 'ai_usage_logs', 'action_items', 'deadlines',
        'email_chunks', 'embeddings', 'rag_retrievals'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS "user_isolation" ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY "user_isolation" ON public.%I FOR ALL TO authenticated '
            || 'USING (user_id = auth.uid() OR public.is_admin()) '
            || 'WITH CHECK (user_id = auth.uid() OR public.is_admin())',
            t
        );
    END LOOP;
END $$;

-- 5b. audit_logs: users may read and append their own rows, never edit them --
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "audit_logs_select" ON public.audit_logs;
CREATE POLICY "audit_logs_select" ON public.audit_logs
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.is_admin());
DROP POLICY IF EXISTS "audit_logs_insert" ON public.audit_logs;
CREATE POLICY "audit_logs_insert" ON public.audit_logs
    FOR INSERT TO authenticated
    WITH CHECK (user_id = auth.uid());
DROP POLICY IF EXISTS "audit_logs_update" ON public.audit_logs;
CREATE POLICY "audit_logs_update" ON public.audit_logs
    FOR UPDATE TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());
DROP POLICY IF EXISTS "audit_logs_delete" ON public.audit_logs;
CREATE POLICY "audit_logs_delete" ON public.audit_logs
    FOR DELETE TO authenticated
    USING (public.is_admin());

-- 5c. Tables owned through their parent email --------------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'ai_analyses', 'extracted_information',
        'email_attachments', 'email_processing_runs'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS "owner_via_email" ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY "owner_via_email" ON public.%I FOR ALL TO authenticated '
            || 'USING (public.is_admin() OR EXISTS ('
            || '  SELECT 1 FROM public.emails e'
            || '  WHERE e.id = public.%I.email_id AND e.user_id = auth.uid())) '
            || 'WITH CHECK (public.is_admin() OR EXISTS ('
            || '  SELECT 1 FROM public.emails e'
            || '  WHERE e.id = public.%I.email_id AND e.user_id = auth.uid()))',
            t, t, t
        );
    END LOOP;
END $$;

-- 5d. ai_conversation_messages: owned through ai_conversations ---------------
ALTER TABLE public.ai_conversation_messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "owner_via_conversation" ON public.ai_conversation_messages;
CREATE POLICY "owner_via_conversation" ON public.ai_conversation_messages
    FOR ALL TO authenticated
    USING (
        public.is_admin()
        OR EXISTS (
            SELECT 1 FROM public.ai_conversations c
            WHERE c.id = ai_conversation_messages.conversation_id
              AND c.user_id = auth.uid()
        )
    )
    WITH CHECK (
        public.is_admin()
        OR EXISTS (
            SELECT 1 FROM public.ai_conversations c
            WHERE c.id = ai_conversation_messages.conversation_id
              AND c.user_id = auth.uid()
        )
    );

-- 5e. Shared reference data: readable by users, managed by admins ------------
ALTER TABLE public.email_categories ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "shared_read" ON public.email_categories;
CREATE POLICY "shared_read" ON public.email_categories
    FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "admin_manage" ON public.email_categories;
CREATE POLICY "admin_manage" ON public.email_categories
    FOR ALL TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());

-- 5f. system_settings: admin manages; users read non-secret settings ---------
ALTER TABLE public.system_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "settings_read" ON public.system_settings;
CREATE POLICY "settings_read" ON public.system_settings
    FOR SELECT TO authenticated
    USING (public.is_admin() OR is_secret = false);
DROP POLICY IF EXISTS "settings_manage" ON public.system_settings;
CREATE POLICY "settings_manage" ON public.system_settings
    FOR ALL TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());

-- 5g. profiles: own profile only -------------------------------------------
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "profiles_select" ON public.profiles;
CREATE POLICY "profiles_select" ON public.profiles
    FOR SELECT TO authenticated
    USING (id = auth.uid() OR public.is_admin());
DROP POLICY IF EXISTS "profiles_insert" ON public.profiles;
CREATE POLICY "profiles_insert" ON public.profiles
    FOR INSERT TO authenticated
    WITH CHECK (id = auth.uid() OR public.is_admin());
DROP POLICY IF EXISTS "profiles_update" ON public.profiles;
CREATE POLICY "profiles_update" ON public.profiles
    FOR UPDATE TO authenticated
    USING (id = auth.uid() OR public.is_admin())
    WITH CHECK (id = auth.uid() OR public.is_admin());
DROP POLICY IF EXISTS "profiles_delete" ON public.profiles;
CREATE POLICY "profiles_delete" ON public.profiles
    FOR DELETE TO authenticated
    USING (public.is_admin());

-- 5h. RBAC tables: readable by users, managed by admins ----------------------
--     (users can always read their own role assignments)
ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "shared_read" ON public.roles;
CREATE POLICY "shared_read" ON public.roles
    FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "admin_manage" ON public.roles;
CREATE POLICY "admin_manage" ON public.roles
    FOR ALL TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());

ALTER TABLE public.permissions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "shared_read" ON public.permissions;
CREATE POLICY "shared_read" ON public.permissions
    FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "admin_manage" ON public.permissions;
CREATE POLICY "admin_manage" ON public.permissions
    FOR ALL TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());

ALTER TABLE public.role_permissions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "shared_read" ON public.role_permissions;
CREATE POLICY "shared_read" ON public.role_permissions
    FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "admin_manage" ON public.role_permissions;
CREATE POLICY "admin_manage" ON public.role_permissions
    FOR ALL TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());

-- user_roles: read own assignments (admins read/write all).
-- Writes are admin-only: a user must never be able to grant themselves ADMIN.
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "user_roles_select" ON public.user_roles;
CREATE POLICY "user_roles_select" ON public.user_roles
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.is_admin());
DROP POLICY IF EXISTS "user_roles_manage" ON public.user_roles;
CREATE POLICY "user_roles_manage" ON public.user_roles
    FOR ALL TO authenticated
    USING (public.is_admin()) WITH CHECK (public.is_admin());

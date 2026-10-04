-- ============================================================================
-- Migration 003: user_roles is the single source of truth for roles
--
-- Problem: the role was maintained in two places (profiles.role + user_roles),
-- requiring sync and guard triggers and risking inconsistency.
--
-- Changes:
--   * DROP profiles.role  -> roles are assigned only in user_roles
--   * DROP profiles.avatar_url (per spec; profile photos are not stored here)
--   * DROP the sync/guard triggers and their functions:
--       protect_profile_role(), sync_profile_role_to_user_roles()
--     Escalation is now prevented purely by RLS: user_roles writes are
--     admin-only (policy "user_roles_manage" from migration 001).
--   * is_admin() rewritten to consult user_roles only
--   * NEW lightweight trigger: assign_default_user_role() gives every new
--     profile the USER role once at creation (no ongoing synchronization)
--
-- Bootstrap an admin from a trusted server-side connection (postgres /
-- service_role, no JWT):
--     INSERT INTO user_roles (user_id, role_id)
--     SELECT '<user-uuid>', id FROM roles WHERE name = 'ADMIN';
--
-- Idempotent. Apply with backend/database/run_migrations.py
-- ============================================================================

-- 1. Remove role-maintenance triggers and their functions --------------------
DROP TRIGGER IF EXISTS protect_profile_role ON public.profiles;
DROP TRIGGER IF EXISTS sync_profile_role_to_user_roles ON public.profiles;
DROP FUNCTION IF EXISTS public.protect_profile_role();
DROP FUNCTION IF EXISTS public.sync_profile_role_to_user_roles();

-- 2. is_admin(): consult user_roles only (must be replaced BEFORE dropping
--    profiles.role, since the old body referenced it) ------------------------
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
        WHERE ur.user_id = auth.uid()
          AND r.name = 'ADMIN'
    );
$$;
REVOKE ALL ON FUNCTION public.is_admin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated, service_role;

-- 3. One-shot default role assignment for new profiles -----------------------
CREATE OR REPLACE FUNCTION public.assign_default_user_role()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.user_roles (user_id, role_id, assigned_by)
    SELECT NEW.id, r.id, auth.uid()
    FROM public.roles r
    WHERE r.name = 'USER'
    ON CONFLICT (user_id, role_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assign_default_user_role ON public.profiles;
CREATE TRIGGER assign_default_user_role
    AFTER INSERT ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.assign_default_user_role();

-- 4. Drop the duplicated / unwanted columns ----------------------------------
ALTER TABLE public.profiles DROP COLUMN IF EXISTS role;
ALTER TABLE public.profiles DROP COLUMN IF EXISTS avatar_url;

-- 5. Drop the now-orphaned user_role enum (only if nothing references it) ----
DO $$
DECLARE
    legacy_user_role oid := to_regtype('public.user_role');
BEGIN
    IF legacy_user_role IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_class c ON a.attrelid = c.oid
        JOIN pg_namespace n ON c.relnamespace = n.oid
        WHERE a.atttypid = legacy_user_role
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND n.nspname <> 'pg_catalog'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_type t
        WHERE t.typname = 'user_role'
          AND pg_type_is_visible(t.oid)
          AND EXISTS (
              SELECT 1 FROM pg_cast c
              WHERE c.casttarget = t.oid OR c.castsource = t.oid
          )
    ) THEN
        DROP TYPE public.user_role;
    END IF;
EXCEPTION
    WHEN dependent_objects_still_exist THEN
        NULL; -- keep the enum if something else still needs it
END $$;

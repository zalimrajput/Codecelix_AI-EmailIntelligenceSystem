-- ============================================================================
-- Migration 007: automatic ADMIN for company-email signups
--
-- Purpose: staff who sign up with an email on a configured company domain
-- (e.g. admin@novatech.com) receive the ADMIN role automatically, so the
-- company admin logs straight into the admin dashboard — no manual UUID
-- bootstrap required.
--
-- How it works:
--   * The company domains live in system_settings key 'admin_email_domains'
--     (non-secret, comma-separated, e.g. "novatech.com,mail.novatech.com").
--     Admins can update it later from the admin dashboard without touching
--     SQL. An empty value disables automatic promotion entirely.
--   * Trigger assign_company_admin_role() fires AFTER INSERT ON profiles
--     (created by the signup trigger handle_new_user()), reads the auth
--     email and inserts the ADMIN role when the domain matches.
--   * Every new profile still keeps the plain USER role from
--     assign_default_user_role() (migration 003) — the roles simply merge.
--   * promote_company_admins() elevates profiles of staff who signed up
--     *before* the domain was configured. Run it once, from a trusted
--     server-side connection, after setting the domain:
--       UPDATE system_settings SET setting_value = 'novatech.com'
--       WHERE setting_key = 'admin_email_domains';
--       SELECT public.promote_company_admins();
--
-- Security note: automatic promotion trusts the domain owner. Only list
-- domains whose mailboxes the company actually controls, and never list a
-- domain belonging to someone else.
--
-- Idempotent. Apply with backend/database/run_migrations.py
-- ============================================================================

-- 1. Configuration key (non-secret, so admins see and can edit it) -----------
INSERT INTO public.system_settings (setting_key, setting_value, description, is_secret)
VALUES (
    'admin_email_domains',
    '',
    'Comma-separated email domains whose signups are automatically granted the ADMIN role (e.g. "novatech.com"). Empty disables automatic promotion.',
    false
)
ON CONFLICT (setting_key) DO NOTHING;

-- 2. Signup-time auto-assignment trigger -------------------------------------
CREATE OR REPLACE FUNCTION public.assign_company_admin_role()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    domain_list text;
    signup_email text;
BEGIN
    SELECT setting_value INTO domain_list
    FROM public.system_settings
    WHERE setting_key = 'admin_email_domains';

    IF domain_list IS NULL OR btrim(domain_list) = '' THEN
        RETURN NEW; -- no company domains configured: plain USER only
    END IF;

    SELECT email INTO signup_email
    FROM auth.users
    WHERE id = NEW.id;

    IF signup_email IS NULL THEN
        RETURN NEW;
    END IF;

    IF lower(btrim(split_part(signup_email, '@', 2))) <> ANY (
        SELECT lower(btrim(d))
        FROM unnest(string_to_array(domain_list, ',')) AS d
    ) THEN
        RETURN NEW; -- personal/non-company email: plain USER only
    END IF;

    INSERT INTO public.user_roles (user_id, role_id, assigned_by)
    SELECT NEW.id, r.id, auth.uid()
    FROM public.roles r
    WHERE r.name = 'ADMIN'
    ON CONFLICT (user_id, role_id) DO NOTHING;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assign_company_admin_role ON public.profiles;
CREATE TRIGGER assign_company_admin_role
    AFTER INSERT ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.assign_company_admin_role();

-- 3. Backfill helper for profiles created before the domain was configured ---
CREATE OR REPLACE FUNCTION public.promote_company_admins()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    domain_list text;
    promoted integer;
BEGIN
    SELECT setting_value INTO domain_list
    FROM public.system_settings
    WHERE setting_key = 'admin_email_domains';

    IF domain_list IS NULL OR btrim(domain_list) = '' THEN
        RETURN 0; -- nothing configured: no-op
    END IF;

    WITH eligible AS (
        SELECT p.id AS user_id
        FROM public.profiles p
        JOIN auth.users u ON u.id = p.id
        WHERE lower(btrim(split_part(u.email, '@', 2))) = ANY (
            SELECT lower(btrim(d))
            FROM unnest(string_to_array(domain_list, ',')) AS d
        )
    ), inserted AS (
        INSERT INTO public.user_roles (user_id, role_id, assigned_by)
        SELECT e.user_id, r.id, auth.uid()
        FROM eligible e
        JOIN public.roles r ON r.name = 'ADMIN'
        ON CONFLICT (user_id, role_id) DO NOTHING
        RETURNING 1
    )
    SELECT count(*) INTO promoted FROM inserted;

    RETURN COALESCE(promoted, 0);
END;
$$;

REVOKE ALL ON FUNCTION public.promote_company_admins() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.promote_company_admins() TO service_role;

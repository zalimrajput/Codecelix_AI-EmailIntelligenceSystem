-- ============================================================================
-- Migration 008: designated-admin email auto-assignment
--
-- Purpose: ONE exact, designated admin email (configured server-side)
-- automatically receives the ADMIN role when its account logs in / signs up,
-- including accounts that already exist. No manual UUID insertion, ever.
--
-- How it works:
--   * `system_settings` key 'authorized_admin_emails' holds the exact admin
--     email address(es), comma-separated, lowercase-matched (non-secret).
--     Empty value = no automatic assignment (default until configured).
--   * Trigger assign_authorized_admin_role() fires AFTER INSERT ON profiles
--     (signup path) and grants ADMIN only when the signup email matches the
--     configured list exactly. Typing an admin-looking email in the signup
--     form grants nothing — only the pre-configured match does, decided
--     entirely by trusted database logic, exactly like the RLS role model.
--   * promote_authorized_admins() elevates accounts created BEFORE the email
--     was configured. Run once from a trusted server-side connection:
--       UPDATE system_settings SET setting_value = 'admin@company.com'
--       WHERE setting_key = 'authorized_admin_emails';
--       SELECT public.promote_authorized_admins();
--
-- Data access remains fully governed by RLS (migration 001–006). Only the
-- role assignment source changes: same user_roles table, same is_admin(),
-- same admin-only write policies — a user still cannot grant themselves
-- any role.
--
-- Security note: list only email addresses whose mailboxes the company
-- actually controls. Configure the address on a domain owned by the
-- company so confirmation emails are receivable.
--
-- Idempotent. Apply with backend/database/run_migrations.py
-- ============================================================================

-- 1. Configuration key (non-secret: admins can see and edit it) --------------
INSERT INTO public.system_settings (setting_key, setting_value, description, is_secret)
VALUES (
    'authorized_admin_emails',
    '',
    'Exact email address(es), comma-separated, that automatically receive the ADMIN role (e.g. "admin@company.com"). Empty disables automatic assignment.',
    false
)
ON CONFLICT (setting_key) DO NOTHING;

-- 2. Signup-time assignment trigger (exact email match) ----------------------
CREATE OR REPLACE FUNCTION public.assign_authorized_admin_role()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    admin_emails text;
    signup_email text;
BEGIN
    SELECT setting_value INTO admin_emails
    FROM public.system_settings
    WHERE setting_key = 'authorized_admin_emails';

    IF admin_emails IS NULL OR btrim(admin_emails) = '' THEN
        RETURN NEW; -- no designated admin configured yet: plain USER only
    END IF;

    SELECT email INTO signup_email
    FROM auth.users
    WHERE id = NEW.id;

    IF signup_email IS NULL THEN
        RETURN NEW;
    END IF;

    IF lower(btrim(signup_email)) <> ANY (
        SELECT lower(btrim(e))
        FROM unnest(string_to_array(admin_emails, ',')) AS e
    ) THEN
        RETURN NEW; -- not the designated admin: plain USER only
    END IF;

    INSERT INTO public.user_roles (user_id, role_id, assigned_by)
    SELECT NEW.id, r.id, auth.uid()
    FROM public.roles r
    WHERE r.name = 'ADMIN'
    ON CONFLICT (user_id, role_id) DO NOTHING;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assign_authorized_admin_role ON public.profiles;
CREATE TRIGGER assign_authorized_admin_role
    AFTER INSERT ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.assign_authorized_admin_role();

-- 3. Backfill for accounts created before the email was configured -----------
CREATE OR REPLACE FUNCTION public.promote_authorized_admins()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    admin_emails text;
    promoted integer;
BEGIN
    SELECT setting_value INTO admin_emails
    FROM public.system_settings
    WHERE setting_key = 'authorized_admin_emails';

    IF admin_emails IS NULL OR btrim(admin_emails) = '' THEN
        RETURN 0; -- nothing configured: no-op
    END IF;

    WITH eligible AS (
        SELECT u.id AS user_id
        FROM auth.users u
        WHERE lower(btrim(u.email)) = ANY (
            SELECT lower(btrim(e))
            FROM unnest(string_to_array(admin_emails, ',')) AS e
        )
    ), inserted AS (
        INSERT INTO public.user_roles (user_id, role_id, assigned_by)
        SELECT eligible.user_id, r.id, auth.uid()
        FROM eligible
        JOIN public.roles r ON r.name = 'ADMIN'
        ON CONFLICT (user_id, role_id) DO NOTHING
        RETURNING 1
    )
    SELECT count(*) INTO promoted FROM inserted;

    RETURN COALESCE(promoted, 0);
END;
$$;

REVOKE ALL ON FUNCTION public.promote_authorized_admins() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.promote_authorized_admins() TO service_role;

-- User workspaces, including an administrator's own workspace, are private.
-- Cross-user admin pages use narrowly scoped SECURITY DEFINER summary RPCs below.
DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'emails', 'email_threads', 'email_senders', 'email_imports',
        'notification_preferences', 'notifications', 'ai_conversations',
        'ai_replies', 'ai_usage_logs', 'action_items', 'deadlines',
        'email_chunks', 'embeddings', 'rag_retrievals'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS "user_isolation" ON public.%I', table_name);
        EXECUTE format(
            'CREATE POLICY "user_isolation" ON public.%I FOR ALL TO authenticated '
            || 'USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())',
            table_name
        );
    END LOOP;
END $$;

DROP POLICY IF EXISTS gmail_connections_owner ON public.gmail_connections;
CREATE POLICY gmail_connections_owner ON public.gmail_connections
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'ai_analyses', 'extracted_information',
        'email_attachments', 'email_processing_runs'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS "owner_via_email" ON public.%I', table_name);
        EXECUTE format(
            'CREATE POLICY "owner_via_email" ON public.%I FOR ALL TO authenticated '
            || 'USING (EXISTS (SELECT 1 FROM public.emails e '
            || 'WHERE e.id = public.%I.email_id AND e.user_id = auth.uid())) '
            || 'WITH CHECK (EXISTS (SELECT 1 FROM public.emails e '
            || 'WHERE e.id = public.%I.email_id AND e.user_id = auth.uid()))',
            table_name, table_name, table_name
        );
    END LOOP;
END $$;

DROP POLICY IF EXISTS owner_via_conversation ON public.ai_conversation_messages;
CREATE POLICY owner_via_conversation ON public.ai_conversation_messages
    FOR ALL TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.ai_conversations c
        WHERE c.id = ai_conversation_messages.conversation_id
          AND c.user_id = auth.uid()
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.ai_conversations c
        WHERE c.id = ai_conversation_messages.conversation_id
          AND c.user_id = auth.uid()
    ));

DROP POLICY IF EXISTS audit_logs_select ON public.audit_logs;
CREATE POLICY audit_logs_select ON public.audit_logs
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS profiles_select ON public.profiles;
CREATE POLICY profiles_select ON public.profiles
    FOR SELECT TO authenticated USING (id = auth.uid());
DROP POLICY IF EXISTS profiles_insert ON public.profiles;
CREATE POLICY profiles_insert ON public.profiles
    FOR INSERT TO authenticated WITH CHECK (id = auth.uid());
DROP POLICY IF EXISTS profiles_update ON public.profiles;
CREATE POLICY profiles_update ON public.profiles
    FOR UPDATE TO authenticated
    USING (id = auth.uid()) WITH CHECK (id = auth.uid());
DROP POLICY IF EXISTS profiles_delete ON public.profiles;
CREATE POLICY profiles_delete ON public.profiles
    FOR DELETE TO authenticated USING (false);

CREATE OR REPLACE FUNCTION public.admin_workspace_overview()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;

    RETURN jsonb_build_object(
        'users', (SELECT count(*) FROM public.profiles),
        'active_users', (SELECT count(*) FROM public.profiles WHERE is_active),
        'emails', (SELECT count(*) FROM public.emails),
        'failed_processing_runs', (
            SELECT count(*)
            FROM public.email_processing_runs r
            JOIN public.emails e ON e.id = r.email_id
            WHERE r.status = 'FAILED'
        ),
        'ai_tokens', COALESCE((SELECT sum(total_tokens) FROM public.ai_usage_logs), 0)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_user_list(result_limit integer DEFAULT 50)
RETURNS TABLE(
    id uuid,
    full_name varchar,
    is_active boolean,
    email_verified boolean,
    created_at timestamptz,
    roles jsonb
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT p.id, p.full_name, p.is_active, p.email_verified, p.created_at,
           COALESCE((
               SELECT jsonb_agg(r.name ORDER BY r.name)
               FROM public.user_roles ur
               JOIN public.roles r ON r.id = ur.role_id
               WHERE ur.user_id = p.id
           ), '[]'::jsonb)
    FROM public.profiles p
    ORDER BY p.created_at DESC
    LIMIT LEAST(GREATEST(result_limit, 1), 100);
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_user_details(target_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    profile_row public.profiles%ROWTYPE;
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO profile_row
    FROM public.profiles
    WHERE id = target_user_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    RETURN jsonb_build_object(
        'id', profile_row.id,
        'full_name', profile_row.full_name,
        'roles', COALESCE((
            SELECT jsonb_agg(r.name ORDER BY r.name)
            FROM public.user_roles ur
            JOIN public.roles r ON r.id = ur.role_id
            WHERE ur.user_id = target_user_id
        ), '[]'::jsonb),
        'account_status', CASE WHEN profile_row.is_active THEN 'ACTIVE' ELSE 'INACTIVE' END,
        'email_verified', profile_row.email_verified,
        'created_at', profile_row.created_at,
        'email_activity', jsonb_build_object(
            'emails_received', (
                SELECT count(*) FROM public.emails e
                WHERE e.user_id = target_user_id AND NOT COALESCE(e.is_deleted, false)
            ),
            'gmail_emails_received', (
                SELECT count(*) FROM public.emails e
                WHERE e.user_id = target_user_id AND NOT COALESCE(e.is_deleted, false)
                  AND e.source_type = 'GMAIL'
            ),
            'emails_processed', (
                SELECT count(DISTINCT r.email_id)
                FROM public.email_processing_runs r
                JOIN public.emails e ON e.id = r.email_id
                WHERE e.user_id = target_user_id AND NOT COALESCE(e.is_deleted, false)
                  AND r.status = 'COMPLETED'
            ),
            'unread_emails', (
                SELECT count(*) FROM public.emails e
                WHERE e.user_id = target_user_id AND NOT COALESCE(e.is_deleted, false)
                  AND e.status = 'UNREAD'
            ),
            'ai_analyses', (
                SELECT count(*)
                FROM public.ai_analyses a
                JOIN public.emails e ON e.id = a.email_id
                WHERE e.user_id = target_user_id AND NOT COALESCE(e.is_deleted, false)
            ),
            'pending_action_items', (
                SELECT count(*) FROM public.action_items a
                WHERE a.user_id = target_user_id AND a.status IN ('PENDING', 'IN_PROGRESS')
            )
        ),
        'ai_usage', jsonb_build_object(
            'requests', (
                SELECT count(*) FROM public.ai_usage_logs u WHERE u.user_id = target_user_id
            ),
            'tokens', COALESCE((
                SELECT sum(u.total_tokens) FROM public.ai_usage_logs u
                WHERE u.user_id = target_user_id
            ), 0)
        ),
        'smart_replies_generated', COALESCE((
            SELECT sum(generation_count) FROM public.ai_replies
            WHERE user_id = target_user_id
        ), 0),
        'last_gmail_sync_at', (
            SELECT gc.last_synced_at FROM public.gmail_connections gc
            WHERE gc.user_id = target_user_id
        )
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_set_user_active(
    target_user_id uuid,
    account_active boolean
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;
    IF target_user_id = auth.uid() AND NOT account_active THEN
        RAISE EXCEPTION 'An administrator cannot deactivate their own account' USING ERRCODE = '22023';
    END IF;

    UPDATE public.profiles
    SET is_active = account_active
    WHERE id = target_user_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    RETURN jsonb_build_object('user_id', target_user_id, 'is_active', account_active);
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_set_user_roles(
    target_user_id uuid,
    requested_roles text[]
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    requested text[];
    resulting_roles jsonb;
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(array_agg(DISTINCT role_name ORDER BY role_name), ARRAY[]::text[])
    INTO requested
    FROM unnest(requested_roles) AS role_values(role_name)
    WHERE role_name IS NOT NULL;
    IF cardinality(requested) = 0 OR NOT (requested <@ ARRAY['ADMIN', 'USER']::text[]) THEN
        RAISE EXCEPTION 'One or more requested roles are not configured' USING ERRCODE = '22023';
    END IF;
    IF target_user_id = auth.uid() AND NOT ('ADMIN' = ANY(requested)) THEN
        RAISE EXCEPTION 'You cannot remove your own administrator role' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id = target_user_id) THEN
        RETURN NULL;
    END IF;

    DELETE FROM public.user_roles ur
    USING public.roles r
    WHERE ur.role_id = r.id AND ur.user_id = target_user_id
      AND NOT (r.name = ANY(requested));
    INSERT INTO public.user_roles(user_id, role_id, assigned_by)
    SELECT target_user_id, r.id, auth.uid()
    FROM public.roles r
    WHERE r.name = ANY(requested)
    ON CONFLICT (user_id, role_id) DO NOTHING;

    SELECT COALESCE(jsonb_agg(r.name ORDER BY r.name), '[]'::jsonb)
    INTO resulting_roles
    FROM public.user_roles ur
    JOIN public.roles r ON r.id = ur.role_id
    WHERE ur.user_id = target_user_id;
    RETURN jsonb_build_object('user_id', target_user_id, 'roles', resulting_roles);
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_ai_usage_summary()
RETURNS TABLE(operation text, ai_model text, requests bigint, total_tokens bigint)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT u.operation::text, u.ai_model::text, count(*)::bigint,
           COALESCE(sum(u.total_tokens), 0)::bigint
    FROM public.ai_usage_logs u
    GROUP BY u.operation, u.ai_model
    ORDER BY u.operation, u.ai_model;
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_recent_audit_logs(result_limit integer DEFAULT 50)
RETURNS TABLE(action text, entity_type text, description text, created_at timestamptz)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT public.is_admin() THEN
        RAISE EXCEPTION 'Administrator role required' USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT l.action::text, l.entity_type::text, l.description::text, l.created_at
    FROM public.audit_logs l
    ORDER BY l.created_at DESC
    LIMIT LEAST(GREATEST(result_limit, 1), 100);
END;
$$;

REVOKE ALL ON FUNCTION public.admin_workspace_overview() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_user_list(integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_user_details(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_set_user_active(uuid, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_set_user_roles(uuid, text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_ai_usage_summary() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_recent_audit_logs(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.admin_workspace_overview() TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_user_list(integer) TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_user_details(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_set_user_active(uuid, boolean) TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_set_user_roles(uuid, text[]) TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_ai_usage_summary() TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_recent_audit_logs(integer) TO authenticated;

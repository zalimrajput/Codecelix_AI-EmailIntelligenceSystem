-- ============================================================================
-- Migration 002: Permission catalog synced to the spec's RBAC tree
--
--   Admin (role receives ALL permissions in the catalog):
--     user management | view/manage all users' emails | system settings |
--     system analytics | audit logs | AI usage / costs | email categories |
--     AI processing configuration | notification configuration |
--     system-wide monitoring
--
--   User (own data only):
--     own emails | import own emails | search/filter own emails |
--     own AI analysis | own summaries | own extracted information |
--     own action items | own deadlines | generate/edit/approve own AI replies |
--     own notifications | own analytics | AI assistant | RAG search over own emails
--
-- Replaces the interim catalog seeded by 001: obsolete permission codes are
-- removed (role_permissions cascade) and the role -> permission mappings are
-- rebuilt. Idempotent. Apply with backend/database/run_migrations.py
-- ============================================================================

CREATE TEMP TABLE _desired_permissions (
    code             text PRIMARY KEY,
    name             text NOT NULL,
    resource         text NOT NULL,
    action           text NOT NULL,
    description      text,
    is_user_default  boolean NOT NULL DEFAULT false
) ON COMMIT DROP;

INSERT INTO _desired_permissions
    (code, name, resource, action, description, is_user_default)
VALUES
    -- ---------------- Admin ----------------
    ('users:manage',            'User management',              'users',           'manage',  'Create, update, deactivate users and assign roles', false),
    ('emails:read_all',         'View all users'' emails',      'emails',          'read_all','Read every user''s emails and threads', false),
    ('emails:manage_all',       'Manage users'' emails',        'emails',          'manage_all', 'Edit, archive or delete any user''s emails', false),
    ('system_settings:manage',  'System settings',              'system_settings', 'manage',  'View and update system settings', false),
    ('system_analytics:read',   'System analytics',             'system_analytics','read',    'View system-wide analytics and dashboards', false),
    ('audit_logs:read',         'Audit logs',                   'audit_logs',      'read',    'View audit logs for all users', false),
    ('ai_usage:read',           'AI usage / AI costs',          'ai_usage',        'read',    'View AI usage and cost logs for all users', false),
    ('email_categories:manage', 'Email categories',             'email_categories','manage',  'Create, update and deactivate email categories', false),
    ('ai_config:manage',        'AI processing configuration',  'ai_config',       'manage',  'Configure AI models, prompts and processing pipelines', false),
    ('notification_config:manage', 'Notification configuration','notification_config', 'manage', 'Configure system-wide notification settings', false),
    ('monitoring:read',         'System-wide monitoring',       'monitoring',      'read',    'View system health, jobs and processing monitoring', false),
    -- ---------------- User (own data) ----------------
    ('emails:read',             'Own emails',                   'emails',          'read',    'Read own emails and threads', true),
    ('emails:import',           'Import own emails',            'emails',          'import',  'Import own emails (EML, CSV, JSON, manual)', true),
    ('emails:search',           'Search/filter own emails',     'emails',          'search',  'Full-text and filter search over own emails', true),
    ('ai_analysis:read',        'Own AI analysis',              'ai_analysis',     'read',    'View AI classification, sentiment and priority for own emails', true),
    ('ai_summaries:read',       'Own summaries',                'ai_summaries',    'read',    'View AI summaries of own emails', true),
    ('extracted_info:read',     'Own extracted information',    'extracted_info',  'read',    'View information extracted from own emails', true),
    ('action_items:manage',     'Own action items',             'action_items',    'manage',  'View and update own action items', true),
    ('deadlines:manage',        'Own deadlines',                'deadlines',       'manage',  'View and update own deadlines', true),
    ('smart_replies:manage',    'Own AI replies',               'smart_replies',   'manage',  'Generate, edit, approve or reject smart replies for own emails', true),
    ('notifications:manage',    'Own notifications',            'notifications',   'manage',  'Read and mark own notifications', true),
    ('analytics:read',          'Own analytics',                'analytics',       'read',    'View personal email and AI usage analytics', true),
    ('ai_assistant:use',        'AI Assistant',                 'ai_assistant',    'use',     'Chat with the AI assistant about own emails', true),
    ('rag_search:use',          'RAG search over own emails',   'rag_search',      'use',     'Semantic vector search over own email chunks', true);

-- Remove permissions that are no longer part of the tree
-- (their role_permissions rows cascade)
DELETE FROM public.permissions p
WHERE NOT EXISTS (SELECT 1 FROM _desired_permissions d WHERE d.code = p.code);

-- Insert / update the catalog (keeps stable permission ids on conflict)
INSERT INTO public.permissions (code, name, resource, action, description)
SELECT code, name, resource, action, description
FROM _desired_permissions
ON CONFLICT (code) DO UPDATE
    SET name        = EXCLUDED.name,
        resource    = EXCLUDED.resource,
        action      = EXCLUDED.action,
        description = EXCLUDED.description;

-- Rebuild role -> permission mappings
DELETE FROM public.role_permissions;

-- ADMIN gets every permission in the catalog
INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
CROSS JOIN public.permissions p
WHERE r.name = 'ADMIN';

-- USER gets exactly the own-data permissions
INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.code IN (
    SELECT code FROM _desired_permissions WHERE is_user_default
)
WHERE r.name = 'USER';

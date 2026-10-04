-- Base schema for a fresh Supabase project.
-- This migration intentionally creates the original application tables before
-- migrations 001-006 add RBAC, RAG, Gmail, and workspace-isolation features.
-- It is safe to run on an existing installation: objects are created only
-- when missing, and existing rows are never dropped or rewritten.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='email_status') THEN
        CREATE TYPE public.email_status AS ENUM ('UNREAD', 'READ', 'ARCHIVED', 'DELETED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='email_priority') THEN
        CREATE TYPE public.email_priority AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='email_sentiment') THEN
        CREATE TYPE public.email_sentiment AS ENUM ('POSITIVE', 'NEUTRAL', 'NEGATIVE', 'ANGRY', 'URGENT');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='action_status') THEN
        CREATE TYPE public.action_status AS ENUM ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='reply_tone') THEN
        CREATE TYPE public.reply_tone AS ENUM ('PROFESSIONAL', 'FRIENDLY', 'SHORT', 'DETAILED', 'APOLOGETIC', 'FORMAL');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='reply_status') THEN
        CREATE TYPE public.reply_status AS ENUM ('DRAFT', 'APPROVED', 'REJECTED', 'EDITED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='processing_status') THEN
        CREATE TYPE public.processing_status AS ENUM ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='import_type') THEN
        CREATE TYPE public.import_type AS ENUM ('EML', 'CSV', 'JSON', 'MANUAL');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname='public' AND t.typname='notification_type') THEN
        CREATE TYPE public.notification_type AS ENUM (
            'URGENT_EMAIL', 'CUSTOMER_COMPLAINT', 'REPLY_REQUIRED',
            'UPCOMING_DEADLINE', 'ACTION_ITEM', 'AI_PROCESSING_COMPLETED', 'SYSTEM'
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.profiles (
    id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name varchar(150),
    phone varchar(50),
    is_active boolean NOT NULL DEFAULT true,
    email_verified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.email_categories (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    name varchar(100) NOT NULL UNIQUE,
    description text,
    is_system boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.email_threads (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    subject varchar(500),
    thread_reference varchar(500),
    first_email_at timestamptz,
    last_email_at timestamptz,
    email_count integer NOT NULL DEFAULT 0,
    is_archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.email_senders (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    email_address varchar(320) NOT NULL,
    display_name varchar(255),
    company_name varchar(255),
    phone_number varchar(50),
    customer_reference varchar(255),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, email_address)
);

CREATE TABLE IF NOT EXISTS public.emails (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    thread_id uuid REFERENCES public.email_threads(id) ON DELETE SET NULL,
    sender_id uuid REFERENCES public.email_senders(id) ON DELETE SET NULL,
    category_id uuid REFERENCES public.email_categories(id) ON DELETE SET NULL,
    sender_email varchar(320),
    sender_name varchar(255),
    receiver_email varchar(320),
    receiver_name varchar(255),
    cc text,
    bcc text,
    subject varchar(500),
    body_text text,
    body_html text,
    preview text,
    message_id varchar(500),
    in_reply_to varchar(500),
    email_date timestamptz,
    status public.email_status NOT NULL DEFAULT 'UNREAD',
    is_starred boolean NOT NULL DEFAULT false,
    is_archived boolean NOT NULL DEFAULT false,
    is_deleted boolean NOT NULL DEFAULT false,
    reply_required boolean NOT NULL DEFAULT false,
    action_required boolean NOT NULL DEFAULT false,
    source_type varchar(50) DEFAULT 'MANUAL',
    raw_email text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.email_imports (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    import_type public.import_type NOT NULL,
    file_name varchar(500),
    total_records integer NOT NULL DEFAULT 0,
    successful_records integer NOT NULL DEFAULT 0,
    failed_records integer NOT NULL DEFAULT 0,
    status public.processing_status NOT NULL DEFAULT 'PENDING',
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS public.email_attachments (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    email_id uuid NOT NULL REFERENCES public.emails(id) ON DELETE CASCADE,
    file_name varchar(500) NOT NULL,
    file_type varchar(100),
    file_size bigint,
    storage_path text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.email_processing_runs (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    email_id uuid NOT NULL REFERENCES public.emails(id) ON DELETE CASCADE,
    status public.processing_status NOT NULL DEFAULT 'PENDING',
    parser_completed boolean NOT NULL DEFAULT false,
    cleaning_completed boolean NOT NULL DEFAULT false,
    classification_completed boolean NOT NULL DEFAULT false,
    summary_completed boolean NOT NULL DEFAULT false,
    extraction_completed boolean NOT NULL DEFAULT false,
    sentiment_completed boolean NOT NULL DEFAULT false,
    priority_completed boolean NOT NULL DEFAULT false,
    action_detection_completed boolean NOT NULL DEFAULT false,
    deadline_detection_completed boolean NOT NULL DEFAULT false,
    error_message text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ai_analyses (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    email_id uuid NOT NULL UNIQUE REFERENCES public.emails(id) ON DELETE CASCADE,
    category_id uuid REFERENCES public.email_categories(id) ON DELETE SET NULL,
    intent varchar(255),
    priority public.email_priority,
    priority_reason text,
    sentiment public.email_sentiment,
    sentiment_confidence numeric,
    short_summary text,
    detailed_summary text,
    reply_required boolean NOT NULL DEFAULT false,
    action_required boolean NOT NULL DEFAULT false,
    ai_model varchar(100),
    processing_time_ms integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.extracted_information (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    email_id uuid NOT NULL REFERENCES public.emails(id) ON DELETE CASCADE,
    customer_name varchar(255),
    company_name varchar(255),
    phone_number varchar(50),
    email_address varchar(320),
    order_number varchar(255),
    invoice_number varchar(255),
    product varchar(500),
    amount numeric,
    currency varchar(10),
    mentioned_date date,
    deadline_date date,
    meeting_date timestamptz,
    location text,
    requested_action text,
    additional_data jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.action_items (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    email_id uuid NOT NULL REFERENCES public.emails(id) ON DELETE CASCADE,
    title varchar(500) NOT NULL,
    description text,
    status public.action_status NOT NULL DEFAULT 'PENDING',
    due_date timestamptz,
    completed_at timestamptz,
    assigned_to uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.deadlines (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    email_id uuid NOT NULL REFERENCES public.emails(id) ON DELETE CASCADE,
    title varchar(500),
    description text,
    original_text varchar(500),
    deadline_at timestamptz,
    is_completed boolean NOT NULL DEFAULT false,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ai_replies (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    email_id uuid NOT NULL REFERENCES public.emails(id) ON DELETE CASCADE,
    thread_id uuid REFERENCES public.email_threads(id) ON DELETE SET NULL,
    tone public.reply_tone NOT NULL DEFAULT 'PROFESSIONAL',
    generated_reply text NOT NULL,
    edited_reply text,
    status public.reply_status NOT NULL DEFAULT 'DRAFT',
    ai_model varchar(100),
    generation_count integer NOT NULL DEFAULT 1,
    approved_at timestamptz,
    rejected_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    gmail_message_id text
);

CREATE TABLE IF NOT EXISTS public.ai_conversations (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    title varchar(255),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ai_conversation_messages (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    conversation_id uuid NOT NULL REFERENCES public.ai_conversations(id) ON DELETE CASCADE,
    role varchar(30) NOT NULL,
    content text NOT NULL,
    referenced_email_ids uuid[],
    ai_model varchar(100),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ai_usage_logs (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    email_id uuid REFERENCES public.emails(id) ON DELETE SET NULL,
    operation varchar(100) NOT NULL,
    ai_model varchar(100),
    input_tokens integer DEFAULT 0,
    output_tokens integer DEFAULT 0,
    total_tokens integer DEFAULT 0,
    processing_time_ms integer,
    estimated_cost numeric,
    status public.processing_status NOT NULL DEFAULT 'COMPLETED',
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.notifications (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    email_id uuid REFERENCES public.emails(id) ON DELETE CASCADE,
    action_item_id uuid REFERENCES public.action_items(id) ON DELETE CASCADE,
    deadline_id uuid REFERENCES public.deadlines(id) ON DELETE CASCADE,
    type public.notification_type NOT NULL,
    title varchar(255) NOT NULL,
    message text,
    is_read boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.notification_preferences (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid NOT NULL UNIQUE REFERENCES public.profiles(id) ON DELETE CASCADE,
    urgent_email boolean NOT NULL DEFAULT true,
    customer_complaint boolean NOT NULL DEFAULT true,
    reply_required boolean NOT NULL DEFAULT true,
    upcoming_deadline boolean NOT NULL DEFAULT true,
    action_item boolean NOT NULL DEFAULT true,
    ai_processing_completed boolean NOT NULL DEFAULT true,
    email_notifications boolean NOT NULL DEFAULT true,
    in_app_notifications boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.audit_logs (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    user_id uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    action varchar(255) NOT NULL,
    entity_type varchar(100),
    entity_id uuid,
    description text,
    ip_address inet,
    user_agent text,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.system_settings (
    id uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    setting_key varchar(255) NOT NULL UNIQUE,
    setting_value text,
    description text,
    is_secret boolean NOT NULL DEFAULT false,
    updated_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_threads_user_id ON public.email_threads(user_id);
CREATE INDEX IF NOT EXISTS idx_email_senders_user_id ON public.email_senders(user_id);
CREATE INDEX IF NOT EXISTS idx_emails_user_id ON public.emails(user_id);
CREATE INDEX IF NOT EXISTS idx_emails_thread_id ON public.emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_email_date ON public.emails(email_date DESC);
CREATE INDEX IF NOT EXISTS idx_emails_message_id ON public.emails(message_id);
CREATE INDEX IF NOT EXISTS idx_email_imports_user_id ON public.email_imports(user_id);
CREATE INDEX IF NOT EXISTS idx_email_processing_runs_email_id ON public.email_processing_runs(email_id);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_category_id ON public.ai_analyses(category_id);
CREATE INDEX IF NOT EXISTS idx_extracted_information_email_id ON public.extracted_information(email_id);
CREATE INDEX IF NOT EXISTS idx_action_items_user_id ON public.action_items(user_id);
CREATE INDEX IF NOT EXISTS idx_action_items_email_id ON public.action_items(email_id);
CREATE INDEX IF NOT EXISTS idx_deadlines_user_id ON public.deadlines(user_id);
CREATE INDEX IF NOT EXISTS idx_deadlines_email_id ON public.deadlines(email_id);
CREATE INDEX IF NOT EXISTS idx_ai_replies_user_id ON public.ai_replies(user_id);
CREATE INDEX IF NOT EXISTS idx_ai_replies_email_id ON public.ai_replies(email_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_user_id ON public.ai_conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_messages_conversation_id
    ON public.ai_conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_ai_usage_logs_user_id ON public.ai_usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id_created_at
    ON public.notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id_created_at
    ON public.audit_logs(user_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, full_name, email_verified)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data ->> 'full_name', ''),
        NEW.email_confirmed_at IS NOT NULL
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'profiles', 'email_threads', 'email_senders', 'emails', 'ai_analyses',
        'extracted_information', 'action_items', 'deadlines', 'ai_replies',
        'ai_conversations', 'notification_preferences', 'system_settings'
    ] LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I ON public.%I',
            CASE table_name
                WHEN 'email_threads' THEN 'update_threads_updated_at'
                WHEN 'email_senders' THEN 'update_senders_updated_at'
                ELSE format('update_%s_updated_at', table_name)
            END,
            table_name
        );
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE ON public.%I '
            || 'FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column()',
            CASE table_name
                WHEN 'email_threads' THEN 'update_threads_updated_at'
                WHEN 'email_senders' THEN 'update_senders_updated_at'
                ELSE format('update_%s_updated_at', table_name)
            END,
            table_name
        );
    END LOOP;
END
$$;

INSERT INTO public.email_categories (name, description, is_system)
VALUES
    ('Customer Complaint', 'Customer complaints and service issues', true),
    ('General Information', 'General information and updates', true),
    ('Invoice / Payment', 'Invoices, billing, and payment messages', true),
    ('Job Application', 'Job applications and recruitment messages', true),
    ('Meeting Request', 'Meeting invitations and scheduling', true),
    ('Other', 'Messages that do not fit another category', true),
    ('Sales Inquiry', 'Sales and product inquiries', true),
    ('Spam', 'Unwanted or suspicious messages', true),
    ('Support Request', 'Support requests and troubleshooting', true),
    ('Urgent', 'Time-sensitive or urgent messages', true)
ON CONFLICT (name) DO NOTHING;

GRANT USAGE ON SCHEMA public TO authenticated;
GRANT USAGE ON SCHEMA extensions TO authenticated;
GRANT EXECUTE ON FUNCTION extensions.uuid_generate_v4() TO authenticated;
GRANT USAGE ON TYPE
    public.email_status, public.email_priority, public.email_sentiment,
    public.action_status, public.reply_tone, public.reply_status,
    public.processing_status, public.import_type, public.notification_type
TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.profiles, public.email_categories, public.email_threads,
    public.email_senders, public.emails, public.email_imports,
    public.email_attachments, public.email_processing_runs,
    public.ai_analyses, public.extracted_information, public.action_items,
    public.deadlines, public.ai_replies, public.ai_conversations,
    public.ai_conversation_messages, public.ai_usage_logs, public.notifications,
    public.notification_preferences, public.audit_logs, public.system_settings
TO authenticated;
REVOKE ALL ON TABLE
    public.profiles, public.email_categories, public.email_threads,
    public.email_senders, public.emails, public.email_imports,
    public.email_attachments, public.email_processing_runs,
    public.ai_analyses, public.extracted_information, public.action_items,
    public.deadlines, public.ai_replies, public.ai_conversations,
    public.ai_conversation_messages, public.ai_usage_logs, public.notifications,
    public.notification_preferences, public.audit_logs, public.system_settings
FROM anon;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_senders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.emails ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_processing_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.extracted_information ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.action_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deadlines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_replies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_usage_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_settings ENABLE ROW LEVEL SECURITY;

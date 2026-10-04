-- Encrypted Gmail credentials and a caller-RLS-scoped vector search function.
-- Refresh tokens are Fernet-encrypted by the FastAPI service before insertion.
CREATE TABLE IF NOT EXISTS public.gmail_connections (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id uuid NOT NULL UNIQUE REFERENCES public.profiles (id) ON DELETE CASCADE,
    google_email varchar(320),
    encrypted_refresh_token text NOT NULL,
    granted_scopes text,
    last_synced_at timestamptz,
    last_history_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.gmail_connections TO authenticated;
REVOKE ALL ON TABLE public.gmail_connections FROM anon;

ALTER TABLE public.gmail_connections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gmail_connections_owner ON public.gmail_connections;
CREATE POLICY gmail_connections_owner ON public.gmail_connections
    FOR ALL TO authenticated
    USING (user_id = auth.uid() OR public.is_admin())
    WITH CHECK (user_id = auth.uid() OR public.is_admin());

DROP TRIGGER IF EXISTS update_gmail_connections_updated_at ON public.gmail_connections;
CREATE TRIGGER update_gmail_connections_updated_at
    BEFORE UPDATE ON public.gmail_connections
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE OR REPLACE FUNCTION public.search_email_chunks(
    query_embedding vector(1536),
    match_count integer DEFAULT 10
)
RETURNS TABLE (
    chunk_id uuid,
    email_id uuid,
    content text,
    similarity double precision,
    subject varchar,
    sender_email varchar,
    email_date timestamptz
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT c.id, c.email_id, c.content,
           1 - (emb.embedding <=> query_embedding) AS similarity,
           e.subject, e.sender_email, e.email_date
    FROM public.embeddings emb
    JOIN public.email_chunks c ON c.id = emb.chunk_id
    JOIN public.emails e ON e.id = c.email_id
    WHERE emb.user_id = auth.uid()
      AND c.user_id = auth.uid()
      AND e.user_id = auth.uid()
      AND NOT COALESCE(e.is_deleted, false)
    ORDER BY emb.embedding <=> query_embedding
    LIMIT LEAST(GREATEST(match_count, 1), 50);
$$;
REVOKE ALL ON FUNCTION public.search_email_chunks(vector, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_email_chunks(vector, integer) TO authenticated;

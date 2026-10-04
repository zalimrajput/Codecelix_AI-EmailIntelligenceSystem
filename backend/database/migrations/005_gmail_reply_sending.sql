ALTER TYPE public.reply_status ADD VALUE IF NOT EXISTS 'SENDING';
ALTER TYPE public.reply_status ADD VALUE IF NOT EXISTS 'SENT';

ALTER TABLE public.ai_replies
    ADD COLUMN IF NOT EXISTS sent_at timestamptz,
    ADD COLUMN IF NOT EXISTS gmail_message_id text;

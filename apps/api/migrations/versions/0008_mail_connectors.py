"""Real mailbox connectors (Microsoft 365 via Graph, Google Workspace via Gmail).

- `mailboxes` gains the connection: how it is connected, the provider account, the change-notification
  stream (Graph subscription or Gmail watch) with a hashed client secret, the catch-up cursor (Graph deltaLink
  or Gmail historyId), health signals, and whether sending is enabled. `credentials_enc` now holds the OAuth
  tokens sealed with the tenant's data key.
- `mail_messages`: every provider message we saw, deduplicated per mailbox, with its identifiers, parsed
  headers, sender-authentication verdict, the raw MIME sealed with the tenant key, and what we did with it.
- `mail_attachments`: attachment metadata; content is not passed on until scanned.
- `mail_sync_events`: an append-only log of notifications, sweeps, fetches, sends, token refreshes and
  errors, for health and support.
- `mail_send_intents`: one row per approved reply to send, created before the provider is called, so a retry
  after a crash never sends twice.
- `ci_mailbox_by_stream`: the narrow cross-tenant lookup a provider notification needs (stream → mailbox).

Revision ID: 0008_mail_connectors
Revises: 0007_workspace_sso
"""

from __future__ import annotations

from alembic import op

revision = "0008_mail_connectors"
down_revision = "0007_workspace_sso"
branch_labels = None
depends_on = None


def _run(sql: str) -> None:
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(
        """
        ALTER TABLE mailboxes
          ADD COLUMN connection text NOT NULL DEFAULT 'not_connected',
          ADD COLUMN connection_mode text NOT NULL DEFAULT 'delegated_oauth',
          ADD COLUMN provider_account text,
          ADD COLUMN stream_id text,
          ADD COLUMN stream_secret_hash text,
          ADD COLUMN stream_expires_at timestamptz,
          ADD COLUMN cursor text,
          ADD COLUMN cursor_updated_at timestamptz,
          ADD COLUMN last_message_at timestamptz,
          ADD COLUMN last_notification_at timestamptz,
          ADD COLUMN last_sweep_at timestamptz,
          ADD COLUMN token_expires_at timestamptz,
          ADD COLUMN last_error text NOT NULL DEFAULT '',
          ADD COLUMN last_error_at timestamptz,
          ADD COLUMN lag_seconds int,
          ADD COLUMN send_enabled boolean NOT NULL DEFAULT false,
          ADD COLUMN last_test_at timestamptz,
          ADD COLUMN last_test_ok_at timestamptz,
          ADD CONSTRAINT mailboxes_connection_ck CHECK (connection in
            ('not_connected', 'connecting', 'syncing', 'live', 'degraded', 'reauth_required', 'disconnected'));
        CREATE UNIQUE INDEX mailboxes_stream_uq ON mailboxes (stream_id) WHERE stream_id is not null;
        ALTER TABLE mailboxes ADD CONSTRAINT mailboxes_org_id_id_uq UNIQUE (org_id, id);

        CREATE TABLE mail_messages (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          mailbox_id uuid NOT NULL,
          provider_message_id text NOT NULL,
          internet_message_id text,
          conversation_id text,
          in_reply_to text,
          references_ text NOT NULL DEFAULT '',
          from_addr text NOT NULL DEFAULT '',
          from_name text NOT NULL DEFAULT '',
          to_addrs jsonb NOT NULL DEFAULT '[]'::jsonb,
          cc_addrs jsonb NOT NULL DEFAULT '[]'::jsonb,
          subject text NOT NULL DEFAULT '',
          received_at timestamptz,
          auth jsonb NOT NULL DEFAULT '{}'::jsonb,
          raw_sealed text,
          raw_size int NOT NULL DEFAULT 0,
          body_text text NOT NULL DEFAULT '',
          direction text NOT NULL DEFAULT 'inbound',
          flags jsonb NOT NULL DEFAULT '[]'::jsonb,
          outcome text NOT NULL,
          ticket_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT mail_messages_mailbox_fk FOREIGN KEY (org_id, mailbox_id)
            REFERENCES mailboxes (org_id, id) ON DELETE CASCADE,
          CONSTRAINT mail_messages_outcome_ck CHECK (outcome in
            ('ticket', 'thread', 'test', 'skipped_auto_reply', 'skipped_bounce', 'skipped_loop', 'skipped_own',
             'quarantined', 'error'))
        );
        CREATE UNIQUE INDEX mail_messages_provider_uq ON mail_messages (org_id, mailbox_id, provider_message_id);
        CREATE INDEX mail_messages_internet_idx ON mail_messages (org_id, internet_message_id);
        CREATE INDEX mail_messages_conversation_idx ON mail_messages (org_id, mailbox_id, conversation_id);

        CREATE TABLE mail_attachments (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          message_id uuid NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
          provider_attachment_id text NOT NULL,
          filename text NOT NULL,
          content_type text NOT NULL DEFAULT 'application/octet-stream',
          size int NOT NULL DEFAULT 0,
          is_inline boolean NOT NULL DEFAULT false,
          av_status text NOT NULL DEFAULT 'not_scanned',
          CONSTRAINT mail_attachments_av_ck CHECK (av_status in ('not_scanned', 'pending', 'clean', 'infected', 'error'))
        );
        CREATE INDEX mail_attachments_message_idx ON mail_attachments (org_id, message_id);

        CREATE TABLE mail_sync_events (
          id bigserial PRIMARY KEY,
          org_id uuid NOT NULL,
          mailbox_id uuid NOT NULL,
          at timestamptz NOT NULL DEFAULT now(),
          kind text NOT NULL,
          ok boolean NOT NULL DEFAULT true,
          detail jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX mail_sync_events_mailbox_idx ON mail_sync_events (org_id, mailbox_id, at DESC);

        CREATE TABLE mail_send_intents (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          mailbox_id uuid NOT NULL,
          ticket_id uuid NOT NULL,
          source text NOT NULL,
          source_id uuid NOT NULL,
          reply_to_provider_id text,
          state text NOT NULL DEFAULT 'pending',
          provider_draft_id text,
          provider_message_id text,
          attempts int NOT NULL DEFAULT 0,
          error text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          sent_at timestamptz,
          CONSTRAINT mail_send_intents_state_ck CHECK (state in ('pending', 'drafted', 'sent', 'failed')),
          CONSTRAINT mail_send_intents_source_ck CHECK (source in ('draft', 'reply'))
        );
        CREATE UNIQUE INDEX mail_send_intents_source_uq ON mail_send_intents (org_id, source, source_id);

        SELECT ci_enable_tenant_rls('mail_messages');
        SELECT ci_enable_tenant_rls('mail_attachments');
        SELECT ci_enable_tenant_rls('mail_sync_events');
        SELECT ci_enable_tenant_rls('mail_send_intents');

        CREATE OR REPLACE FUNCTION ci_mailbox_by_stream(s text)
          RETURNS TABLE (org_id uuid, mailbox_id uuid, secret_hash text, provider text)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT org_id, id, stream_secret_hash, provider FROM mailboxes WHERE stream_id = s LIMIT 1
        $$;
        REVOKE ALL ON FUNCTION ci_mailbox_by_stream(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ci_mailbox_by_stream(text) TO ci_app;

        CREATE OR REPLACE FUNCTION ci_mailbox_by_account(a text)
          RETURNS TABLE (org_id uuid, mailbox_id uuid, provider text)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT org_id, id, provider FROM mailboxes WHERE lower(provider_account) = lower(a) LIMIT 1
        $$;
        REVOKE ALL ON FUNCTION ci_mailbox_by_account(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ci_mailbox_by_account(text) TO ci_app;

        CREATE OR REPLACE FUNCTION ci_live_mailboxes() RETURNS TABLE (org_id uuid, mailbox_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT org_id, id FROM mailboxes WHERE connection in ('syncing', 'live', 'degraded')
        $$;
        REVOKE ALL ON FUNCTION ci_live_mailboxes() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ci_live_mailboxes() TO ci_app
        """
    )


def downgrade() -> None:
    _run(
        """
        DROP FUNCTION IF EXISTS ci_live_mailboxes();
        DROP FUNCTION IF EXISTS ci_mailbox_by_account(text);
        DROP FUNCTION IF EXISTS ci_mailbox_by_stream(text);
        DROP TABLE mail_send_intents;
        DROP TABLE mail_sync_events;
        DROP TABLE mail_attachments;
        DROP TABLE mail_messages;
        DROP INDEX IF EXISTS mailboxes_stream_uq;
        ALTER TABLE mailboxes DROP CONSTRAINT mailboxes_org_id_id_uq;
        ALTER TABLE mailboxes
          DROP CONSTRAINT mailboxes_connection_ck,
          DROP COLUMN last_test_ok_at,
          DROP COLUMN last_test_at,
          DROP COLUMN send_enabled,
          DROP COLUMN lag_seconds,
          DROP COLUMN last_error_at,
          DROP COLUMN last_error,
          DROP COLUMN token_expires_at,
          DROP COLUMN last_sweep_at,
          DROP COLUMN last_notification_at,
          DROP COLUMN last_message_at,
          DROP COLUMN cursor_updated_at,
          DROP COLUMN cursor,
          DROP COLUMN stream_expires_at,
          DROP COLUMN stream_secret_hash,
          DROP COLUMN stream_id,
          DROP COLUMN provider_account,
          DROP COLUMN connection_mode,
          DROP COLUMN connection
        """
    )

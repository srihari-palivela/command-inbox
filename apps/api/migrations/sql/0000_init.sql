CREATE TABLE "action_instances" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"template_id" uuid NOT NULL,
	"account_ref" text DEFAULT '' NOT NULL,
	"fields" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"validation" text DEFAULT '' NOT NULL,
	"state" text NOT NULL,
	"chain" text NOT NULL,
	"idempotency_key" text NOT NULL,
	"maker_id" uuid,
	"maker_at" timestamp with time zone,
	"checker_id" uuid,
	"checker_at" timestamp with time zone,
	"execute_after" timestamp with time zone,
	"executed_at" timestamp with time zone,
	"external_ref" text,
	"failure" text,
	"rejected_note" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "action_templates" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"code" text NOT NULL,
	"name" text NOT NULL,
	"system" text NOT NULL,
	"endpoint" text DEFAULT '' NOT NULL,
	"owner" text DEFAULT '' NOT NULL,
	"reversible" boolean NOT NULL,
	"money_moves" boolean NOT NULL,
	"approval" text NOT NULL,
	"stp_pct" integer,
	"monthly_volume" integer DEFAULT 0 NOT NULL,
	"state" text DEFAULT 'active' NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "agent_boards" (
	"org_id" uuid NOT NULL,
	"agent_id" uuid NOT NULL,
	"board_id" uuid NOT NULL,
	CONSTRAINT "agent_boards_org_id_agent_id_board_id_pk" PRIMARY KEY("org_id","agent_id","board_id")
);
--> statement-breakpoint
CREATE TABLE "agent_evals" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"agent_id" uuid NOT NULL,
	"label" text NOT NULL,
	"value" text NOT NULL,
	"tone" text NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "agent_versions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"agent_id" uuid NOT NULL,
	"version" integer NOT NULL,
	"prompt" text NOT NULL,
	"model" text NOT NULL,
	"eval_status" text NOT NULL,
	"created_by" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "agents" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"name" text NOT NULL,
	"abbr" text NOT NULL,
	"template" text NOT NULL,
	"model" text NOT NULL,
	"state" text NOT NULL,
	"version" integer DEFAULT 1 NOT NULL,
	"prompt" text NOT NULL,
	"eval_score" integer,
	"cost_per_1k_minor" integer DEFAULT 0 NOT NULL,
	"role" text DEFAULT '' NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "alerts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"sev_label" text NOT NULL,
	"sev_kind" text NOT NULL,
	"bucket" text NOT NULL,
	"text" text NOT NULL,
	"action_label" text NOT NULL,
	"owner" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"resolved_at" timestamp with time zone,
	"resolved_by" uuid
);
--> statement-breakpoint
CREATE TABLE "approvals" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"subject_kind" text NOT NULL,
	"subject_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"step" text NOT NULL,
	"opened_evidence" boolean NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "attachments" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"ext" text NOT NULL,
	"name" text NOT NULL,
	"size" text NOT NULL,
	"storage_key" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "audit_events" (
	"seq" bigserial PRIMARY KEY NOT NULL,
	"id" uuid DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"at" timestamp with time zone DEFAULT now() NOT NULL,
	"actor_kind" text NOT NULL,
	"actor_id" uuid,
	"actor_name" text NOT NULL,
	"action" text NOT NULL,
	"entity" text NOT NULL,
	"entity_id" text,
	"ticket_id" uuid,
	"summary" text NOT NULL,
	"feed_tone" text,
	"feed_meta" text,
	"data" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"prev_hash" text NOT NULL,
	"hash" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "autonomy_dial" (
	"org_id" uuid NOT NULL,
	"cell" text NOT NULL,
	"level" integer NOT NULL,
	"locked" boolean DEFAULT false NOT NULL,
	"count_override" integer,
	"updated_by" uuid,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "autonomy_dial_org_id_cell_pk" PRIMARY KEY("org_id","cell")
);
--> statement-breakpoint
CREATE TABLE "boards" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"key" text NOT NULL,
	"name" text NOT NULL,
	"mailbox_id" uuid,
	"team" text DEFAULT '' NOT NULL,
	"state" text NOT NULL,
	"auto_rate_pct" integer DEFAULT 0 NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "briefs" (
	"ticket_id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"why" text NOT NULL,
	"summary" text NOT NULL,
	"context" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"suggestions" jsonb DEFAULT '[]'::jsonb NOT NULL
);
--> statement-breakpoint
CREATE TABLE "bucket_rules" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"sort" integer NOT NULL,
	"description" text NOT NULL,
	"target" text NOT NULL,
	"kind" text NOT NULL,
	"hits" text DEFAULT '' NOT NULL,
	"pattern" jsonb
);
--> statement-breakpoint
CREATE TABLE "calls" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"started_by" uuid NOT NULL,
	"state" text NOT NULL,
	"started_at" timestamp with time zone DEFAULT now() NOT NULL,
	"live_at" timestamp with time zone,
	"ended_at" timestamp with time zone,
	"duration_sec" integer DEFAULT 0 NOT NULL,
	"script" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"summary" text,
	"updates" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"recording_key" text
);
--> statement-breakpoint
CREATE TABLE "clearances" (
	"org_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"department_id" uuid NOT NULL,
	"level" integer NOT NULL,
	CONSTRAINT "clearances_org_id_user_id_department_id_pk" PRIMARY KEY("org_id","user_id","department_id")
);
--> statement-breakpoint
CREATE TABLE "comments" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"kind" text NOT NULL,
	"author_id" uuid,
	"author_name" text NOT NULL,
	"author_initials" text NOT NULL,
	"body" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "connectors" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"abbr" text NOT NULL,
	"name" text NOT NULL,
	"scope" text NOT NULL,
	"state" text NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "counters" (
	"org_id" uuid NOT NULL,
	"name" text NOT NULL,
	"value" integer NOT NULL,
	CONSTRAINT "counters_org_id_name_pk" PRIMARY KEY("org_id","name")
);
--> statement-breakpoint
CREATE TABLE "course_completions" (
	"org_id" uuid NOT NULL,
	"course_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"score" integer NOT NULL,
	"total" integer NOT NULL,
	"completed_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "course_completions_org_id_course_id_user_id_pk" PRIMARY KEY("org_id","course_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "courses" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"key" text NOT NULL,
	"title" text NOT NULL,
	"minutes" integer NOT NULL,
	"cards" jsonb NOT NULL,
	"quiz" jsonb NOT NULL,
	"baseline_pct" integer DEFAULT 0 NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "customers" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"cif" text NOT NULL,
	"name" text NOT NULL,
	"email" text NOT NULL,
	"phone" text DEFAULT '' NOT NULL,
	"segment" text NOT NULL,
	"since_year" integer NOT NULL,
	"account" text DEFAULT '' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "daily_metrics" (
	"org_id" uuid NOT NULL,
	"metric" text NOT NULL,
	"day" date NOT NULL,
	"value" double precision NOT NULL,
	CONSTRAINT "daily_metrics_org_id_metric_day_pk" PRIMARY KEY("org_id","metric","day")
);
--> statement-breakpoint
CREATE TABLE "departments" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"name" text NOT NULL,
	"owner_id" uuid,
	"sort" integer DEFAULT 0 NOT NULL,
	"readiness_pct" integer DEFAULT 0 NOT NULL,
	"readiness_note" text DEFAULT '' NOT NULL,
	"gap_note" text DEFAULT '' NOT NULL,
	"risk" boolean DEFAULT false NOT NULL,
	"in_matrix" boolean DEFAULT true NOT NULL
);
--> statement-breakpoint
CREATE TABLE "drafts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"subject" text NOT NULL,
	"to_addr" text NOT NULL,
	"original_body" text NOT NULL,
	"current_body" text NOT NULL,
	"citations" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"flagged" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"state" text NOT NULL,
	"send_after" timestamp with time zone,
	"sent_at" timestamp with time zone,
	"sent_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "drafts_ticket_id_unique" UNIQUE("ticket_id")
);
--> statement-breakpoint
CREATE TABLE "feedback" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"agent_id" uuid,
	"agent_name" text NOT NULL,
	"ticket_id" uuid,
	"ticket_number" text,
	"kind" text NOT NULL,
	"text" text NOT NULL,
	"fix" text NOT NULL,
	"status" text DEFAULT 'open' NOT NULL,
	"diff" jsonb,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "gap_tickets" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"number" integer NOT NULL,
	"severity" text NOT NULL,
	"question" text NOT NULL,
	"detail" text NOT NULL,
	"hits" integer DEFAULT 0 NOT NULL,
	"owner" text NOT NULL,
	"state" text NOT NULL,
	"cta" text NOT NULL,
	"opened_at" timestamp with time zone DEFAULT now() NOT NULL,
	"closed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "idempotency_keys" (
	"org_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"key" text NOT NULL,
	"route" text NOT NULL,
	"request_hash" text NOT NULL,
	"status_code" integer NOT NULL,
	"response" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "idempotency_keys_org_id_user_id_key_pk" PRIMARY KEY("org_id","user_id","key")
);
--> statement-breakpoint
CREATE TABLE "inbound_messages" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"mailbox_id" uuid NOT NULL,
	"provider_message_id" text NOT NULL,
	"ticket_id" uuid,
	"received_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "jobs" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"kind" text NOT NULL,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"run_at" timestamp with time zone DEFAULT now() NOT NULL,
	"state" text DEFAULT 'queued' NOT NULL,
	"attempts" integer DEFAULT 0 NOT NULL,
	"max_attempts" integer DEFAULT 5 NOT NULL,
	"locked_by" text,
	"locked_at" timestamp with time zone,
	"last_error" text,
	"dedupe_key" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "knowledge_docs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"source_id" uuid,
	"title" text NOT NULL,
	"section" text DEFAULT '' NOT NULL,
	"owner" text DEFAULT '' NOT NULL,
	"department_id" uuid,
	"status" text NOT NULL,
	"verified_at" timestamp with time zone,
	"body" text DEFAULT '' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "knowledge_sources" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"name" text NOT NULL,
	"kind" text NOT NULL,
	"abbr" text NOT NULL,
	"doc_count" integer DEFAULT 0 NOT NULL,
	"doc_unit" text DEFAULT 'documents' NOT NULL,
	"approved_count" integer DEFAULT 0 NOT NULL,
	"health" text NOT NULL,
	"sync_note" text DEFAULT '' NOT NULL,
	"note" text DEFAULT '' NOT NULL,
	"last_sync_at" timestamp with time zone,
	"sort" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "kpis" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"owner_id" uuid NOT NULL,
	"name" text NOT NULL,
	"metric" text NOT NULL,
	"viz" text NOT NULL,
	"scope" text NOT NULL,
	"target" double precision NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "mailboxes" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"address" text NOT NULL,
	"provider" text NOT NULL,
	"department_id" uuid,
	"team_label" text DEFAULT '' NOT NULL,
	"permissions" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"state" text NOT NULL,
	"volume_24h" integer DEFAULT 0 NOT NULL,
	"credentials_enc" text,
	"last_sync_at" timestamp with time zone,
	"sort" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "memberships" (
	"org_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"role" text NOT NULL,
	"title" text DEFAULT '' NOT NULL,
	"pod" text DEFAULT '' NOT NULL,
	"capacity" integer DEFAULT 12 NOT NULL,
	"years" integer DEFAULT 1 NOT NULL,
	"base_load" integer DEFAULT 0 NOT NULL,
	"joined_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "memberships_org_id_user_id_pk" PRIMARY KEY("org_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "messages" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"direction" text NOT NULL,
	"from_name" text NOT NULL,
	"from_addr" text NOT NULL,
	"to_addr" text NOT NULL,
	"body" text NOT NULL,
	"sent_at" timestamp with time zone NOT NULL,
	"provider_message_id" text
);
--> statement-breakpoint
CREATE TABLE "notification_reads" (
	"org_id" uuid NOT NULL,
	"notification_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"read_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "notification_reads_org_id_notification_id_user_id_pk" PRIMARY KEY("org_id","notification_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "notifications" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"kind" text NOT NULL,
	"source" text NOT NULL,
	"title" text NOT NULL,
	"body" text DEFAULT '' NOT NULL,
	"course_id" uuid,
	"urgent" boolean DEFAULT false NOT NULL,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "orgs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"short" text NOT NULL,
	"tint" text NOT NULL,
	"bg" text NOT NULL,
	"plan" text NOT NULL,
	"confidence_bar" double precision DEFAULT 0.78 NOT NULL,
	"headcount" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "orgs_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "outbox" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"topic" text NOT NULL,
	"payload" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"dispatched_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "prediction_outcomes" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"agent_name" text NOT NULL,
	"ticket_id" uuid,
	"confidence" double precision NOT NULL,
	"correct" boolean,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "priority_rules" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"key" text NOT NULL,
	"sort" integer NOT NULL,
	"description" text NOT NULL,
	"target" text NOT NULL,
	"hard" boolean NOT NULL,
	"enabled" boolean DEFAULT true NOT NULL,
	"hits" text DEFAULT '' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "proposed_rules" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"text" text NOT NULL,
	"ticket_id" uuid,
	"ticket_number" text,
	"proposed_by" uuid NOT NULL,
	"proposed_by_name" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"decided_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "query_types" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"name" text NOT NULL,
	"map_name" text,
	"department_id" uuid,
	"default_lane" text NOT NULL,
	"monthly_volume" integer DEFAULT 0 NOT NULL,
	"live" boolean DEFAULT true NOT NULL,
	"baseline_hours" double precision,
	"actual_hours" double precision,
	"late_count" integer DEFAULT 0 NOT NULL,
	"owner_label" text DEFAULT '' NOT NULL,
	"show_on_map" boolean DEFAULT true NOT NULL,
	"show_on_speed" boolean DEFAULT false NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "replies" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"body" text NOT NULL,
	"state" text NOT NULL,
	"send_after" timestamp with time zone NOT NULL,
	"sent_at" timestamp with time zone,
	"author_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "sessions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"org_id" uuid NOT NULL,
	"token_hash" text NOT NULL,
	"csrf_token" text NOT NULL,
	"user_agent" text DEFAULT '' NOT NULL,
	"device" text DEFAULT '' NOT NULL,
	"location" text DEFAULT '' NOT NULL,
	"ip" text DEFAULT '' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_seen_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"revoked_at" timestamp with time zone,
	CONSTRAINT "sessions_token_hash_unique" UNIQUE("token_hash")
);
--> statement-breakpoint
CREATE TABLE "staff_availability" (
	"org_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"status" text NOT NULL,
	"checkin" text DEFAULT '' NOT NULL,
	"calendar" text DEFAULT '' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "staff_availability_org_id_user_id_pk" PRIMARY KEY("org_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "subtasks" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"key" text NOT NULL,
	"label" text NOT NULL,
	"owner" text NOT NULL,
	"sort" integer DEFAULT 0 NOT NULL,
	"done" boolean DEFAULT false NOT NULL,
	"done_by" uuid,
	"done_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "ticket_links" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"kind" text NOT NULL,
	"label" text NOT NULL,
	"ref" text,
	"sort" integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "tickets" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"number" integer NOT NULL,
	"board_id" uuid,
	"mailbox_id" uuid,
	"customer_id" uuid,
	"subject" text NOT NULL,
	"from_name" text NOT NULL,
	"from_email" text NOT NULL,
	"received_at" timestamp with time zone NOT NULL,
	"lane" text NOT NULL,
	"original_lane" text NOT NULL,
	"lane_note" text DEFAULT '' NOT NULL,
	"status" text NOT NULL,
	"priority" text NOT NULL,
	"segment" text DEFAULT 'Retail' NOT NULL,
	"department_id" uuid,
	"query_type_id" uuid,
	"bucket" text DEFAULT '' NOT NULL,
	"confidence" double precision DEFAULT 0 NOT NULL,
	"assignee_id" uuid,
	"owner_kind" text NOT NULL,
	"sla_minutes" integer NOT NULL,
	"due_at" timestamp with time zone,
	"paused_at" timestamp with time zone,
	"first_reply_at" timestamp with time zone,
	"resolved_at" timestamp with time zone,
	"closed_at" timestamp with time zone,
	"reopen_count" integer DEFAULT 0 NOT NULL,
	"regulatory_flag" text,
	"resolution" text,
	"sentiment" text DEFAULT 'neutral' NOT NULL,
	"next_move" text DEFAULT '' NOT NULL,
	"category" text DEFAULT '' NOT NULL,
	"subcategory" text DEFAULT '' NOT NULL,
	"product" text DEFAULT '' NOT NULL,
	"parent_id" uuid,
	"merged_into_id" uuid,
	"split_proposed" boolean DEFAULT false NOT NULL,
	"accepted_at" timestamp with time zone,
	"logged_minutes" integer DEFAULT 0 NOT NULL,
	"version" integer DEFAULT 1 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "trace_spans" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"run_id" uuid NOT NULL,
	"seq" integer NOT NULL,
	"offset_ms" integer NOT NULL,
	"agent" text NOT NULL,
	"model" text NOT NULL,
	"action" text NOT NULL,
	"output" text NOT NULL,
	"latency_ms" integer NOT NULL,
	"tokens" integer,
	"cost_minor" integer,
	"status" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "triage_runs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"trace_id" text NOT NULL,
	"reasoning" text NOT NULL,
	"evidence" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"confidence" double precision NOT NULL,
	"lane" text NOT NULL,
	"latency_ms" integer NOT NULL,
	"cost_minor" integer DEFAULT 0 NOT NULL,
	"provider" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "user_settings" (
	"org_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"prefs" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"signature" text DEFAULT '' NOT NULL,
	CONSTRAINT "user_settings_org_id_user_id_pk" PRIMARY KEY("org_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "users" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"email" text NOT NULL,
	"name" text NOT NULL,
	"initials" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "users_email_unique" UNIQUE("email")
);
--> statement-breakpoint
CREATE TABLE "watchers" (
	"org_id" uuid NOT NULL,
	"ticket_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	CONSTRAINT "watchers_org_id_ticket_id_user_id_pk" PRIMARY KEY("org_id","ticket_id","user_id")
);
--> statement-breakpoint
ALTER TABLE "memberships" ADD CONSTRAINT "memberships_org_id_orgs_id_fk" FOREIGN KEY ("org_id") REFERENCES "public"."orgs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "memberships" ADD CONSTRAINT "memberships_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_org_id_orgs_id_fk" FOREIGN KEY ("org_id") REFERENCES "public"."orgs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "action_instances_idem_uq" ON "action_instances" USING btree ("org_id","idempotency_key");--> statement-breakpoint
CREATE INDEX "action_instances_ticket_idx" ON "action_instances" USING btree ("org_id","ticket_id");--> statement-breakpoint
CREATE UNIQUE INDEX "action_templates_code_uq" ON "action_templates" USING btree ("org_id","code");--> statement-breakpoint
CREATE INDEX "audit_org_seq_idx" ON "audit_events" USING btree ("org_id","seq");--> statement-breakpoint
CREATE INDEX "audit_ticket_idx" ON "audit_events" USING btree ("org_id","ticket_id");--> statement-breakpoint
CREATE INDEX "comments_ticket_idx" ON "comments" USING btree ("org_id","ticket_id");--> statement-breakpoint
CREATE UNIQUE INDEX "customers_org_cif_uq" ON "customers" USING btree ("org_id","cif");--> statement-breakpoint
CREATE INDEX "customers_email_idx" ON "customers" USING btree ("org_id","email");--> statement-breakpoint
CREATE UNIQUE INDEX "inbound_dedup_uq" ON "inbound_messages" USING btree ("org_id","mailbox_id","provider_message_id");--> statement-breakpoint
CREATE INDEX "jobs_ready_idx" ON "jobs" USING btree ("state","run_at");--> statement-breakpoint
CREATE UNIQUE INDEX "jobs_dedupe_uq" ON "jobs" USING btree ("org_id","dedupe_key");--> statement-breakpoint
CREATE UNIQUE INDEX "mailboxes_org_address_uq" ON "mailboxes" USING btree ("org_id","address");--> statement-breakpoint
CREATE INDEX "messages_ticket_idx" ON "messages" USING btree ("org_id","ticket_id");--> statement-breakpoint
CREATE INDEX "outbox_pending_idx" ON "outbox" USING btree ("dispatched_at","id");--> statement-breakpoint
CREATE INDEX "sessions_user_idx" ON "sessions" USING btree ("user_id");--> statement-breakpoint
CREATE UNIQUE INDEX "subtasks_ticket_key_uq" ON "subtasks" USING btree ("org_id","ticket_id","key");--> statement-breakpoint
CREATE UNIQUE INDEX "tickets_org_number_uq" ON "tickets" USING btree ("org_id","number");--> statement-breakpoint
CREATE INDEX "tickets_status_idx" ON "tickets" USING btree ("org_id","status");--> statement-breakpoint
CREATE INDEX "tickets_assignee_idx" ON "tickets" USING btree ("org_id","assignee_id");--> statement-breakpoint
CREATE INDEX "tickets_customer_idx" ON "tickets" USING btree ("org_id","customer_id");--> statement-breakpoint
CREATE INDEX "tickets_due_idx" ON "tickets" USING btree ("org_id","due_at");--> statement-breakpoint
CREATE INDEX "trace_spans_run_idx" ON "trace_spans" USING btree ("org_id","run_id");--> statement-breakpoint
CREATE INDEX "triage_runs_ticket_idx" ON "triage_runs" USING btree ("org_id","ticket_id");
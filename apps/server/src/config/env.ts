import { z } from 'zod';

const Env = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().default(4000),
  HOST: z.string().default('0.0.0.0'),
  /** Runtime connection — a non-owner role, so row-level security applies. */
  DATABASE_URL: z.string().default('postgres://ci_app:ci_app@localhost:5432/command_inbox'),
  /** Migration/seed connection — the schema owner. */
  DATABASE_ADMIN_URL: z.string().default('postgres://postgres:postgres@localhost:5432/command_inbox'),
  DB_POOL_MAX: z.coerce.number().int().default(10),
  WEB_ORIGIN: z.string().default('http://localhost:5173'),
  COOKIE_SECURE: z
    .enum(['true', 'false'])
    .default('false')
    .transform((v) => v === 'true'),
  SESSION_TTL_HOURS: z.coerce.number().int().default(12),
  /** Demo mode: passwordless sign-in as any seeded person, and the role switch in the chrome. */
  DEMO_MODE: z
    .enum(['true', 'false'])
    .default('true')
    .transform((v) => v === 'true'),
  /** 32-byte key (hex or base64) for encrypting OAuth tokens at rest. KMS-provided in production. */
  ENCRYPTION_KEY: z.string().default('dev-only-key-change-me-dev-only-key-change-me'),
  INTAKE_WEBHOOK_SECRET: z.string().default('dev-intake-secret'),
  ANTHROPIC_API_KEY: z.string().optional(),
  /** heuristic | claude | auto (claude when a key is present) */
  LLM_PROVIDER: z.enum(['auto', 'claude', 'heuristic']).default('auto'),
  COPILOT_MODEL: z.string().default('claude-opus-5'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent']).default('info'),
  /** Run the job worker inside the API process (convenient for dev; separate process in prod). */
  EMBEDDED_WORKER: z
    .enum(['true', 'false'])
    .default('true')
    .transform((v) => v === 'true'),
  MS_CLIENT_ID: z.string().optional(),
  MS_CLIENT_SECRET: z.string().optional(),
  GOOGLE_CLIENT_ID: z.string().optional(),
  GOOGLE_CLIENT_SECRET: z.string().optional(),
  PUBLIC_API_URL: z.string().default('http://localhost:4000'),
});

export type Env = z.infer<typeof Env>;

export function loadEnv(source: NodeJS.ProcessEnv = process.env): Env {
  const parsed = Env.safeParse(source);
  if (!parsed.success) {
    throw new Error('Invalid environment: ' + JSON.stringify(parsed.error.issues, null, 2));
  }
  const env = parsed.data;
  if (env.NODE_ENV === 'production') {
    if (env.ENCRYPTION_KEY.startsWith('dev-only'))
      throw new Error('ENCRYPTION_KEY must be set in production');
    if (env.INTAKE_WEBHOOK_SECRET === 'dev-intake-secret')
      throw new Error('INTAKE_WEBHOOK_SECRET must be set in production');
    if (!env.COOKIE_SECURE) throw new Error('COOKIE_SECURE must be true in production');
  }
  return env;
}

export const env = loadEnv();

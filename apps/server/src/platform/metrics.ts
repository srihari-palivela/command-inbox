import client from 'prom-client';

export const registry = new client.Registry();
client.collectDefaultMetrics({ register: registry });

export const httpDuration = new client.Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP request latency',
  labelNames: ['method', 'route', 'status'],
  buckets: [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5],
  registers: [registry],
});

export const jobsProcessed = new client.Counter({
  name: 'jobs_processed_total',
  help: 'Jobs processed by kind and outcome',
  labelNames: ['kind', 'outcome'],
  registers: [registry],
});

export const triageDuration = new client.Histogram({
  name: 'triage_duration_seconds',
  help: 'Triage pipeline end-to-end latency',
  labelNames: ['lane'],
  buckets: [0.1, 0.5, 1, 2, 5, 8, 15, 30],
  registers: [registry],
});

export const gateDecisions = new client.Counter({
  name: 'gate_decisions_total',
  help: 'Approval gateway decisions',
  labelNames: ['mode', 'outcome', 'opened_evidence'],
  registers: [registry],
});

export const sseClients = new client.Gauge({ name: 'sse_clients', help: 'Connected SSE clients', registers: [registry] });

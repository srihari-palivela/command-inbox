/* global __ENV, __VU, __ITER */
// Load test at 10x the pilot volume (plan §16, Phase 6). Two scenarios run together:
//   intake  — signed customer mail arriving through the intake webhook (the ingest path)
//   people  — staff and leads working the queue (inbox, tickets, a ticket, monitoring)
// Thresholds are the SLOs: the run fails if they are missed.
//
//   BASE_URL=https://inbox.staging.example INTAKE_SECRET=... MAILBOX=support@bank.example \
//   STAFF_EMAIL=p.sharma@bank.example k6 run tests/load/k6-pilot.js
//
// Pilot volume assumption: 2,000 mails a day (~1.4 a minute, peaking at 3x) and 40 people. 10x: 14 mails a
// minute sustained with 42 at peak, and 400 active users. STAFF sign-in uses the demo login, so run against
// a staging stack in demo mode, or replace login() with your SSO test-user flow.
import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, sleep } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:4000';
const SECRET = __ENV.INTAKE_SECRET || 'dev-intake-secret';
const MAILBOX = __ENV.MAILBOX || 'support@bank.example';
const STAFF = (__ENV.STAFF_EMAIL || 'p.sharma@bank.example').split(',');

export const options = {
  scenarios: {
    intake: {
      executor: 'ramping-arrival-rate',
      exec: 'intake',
      startRate: 14,
      timeUnit: '1m',
      preAllocatedVUs: 10,
      stages: [
        { target: 14, duration: '5m' },
        { target: 42, duration: '5m' }, // peak
        { target: 14, duration: '5m' },
      ],
    },
    people: {
      executor: 'ramping-vus',
      exec: 'people',
      startVUs: 0,
      stages: [
        { target: 400, duration: '3m' },
        { target: 400, duration: '10m' },
        { target: 0, duration: '2m' },
      ],
    },
  },
  thresholds: {
    'http_req_failed{scenario:intake}': ['rate<0.001'], // webhook 2xx >= 99.9%
    'http_req_duration{scenario:intake}': ['p(99)<3000'], // within 3 s
    'http_req_failed{scenario:people}': ['rate<0.001'], // API availability 99.9%
    'http_req_duration{scenario:people}': ['p(95)<800', 'p(99)<2000'],
    checks: ['rate>0.999'],
  },
};

export function intake() {
  const n = `${Date.now()}-${__VU}-${__ITER}`;
  const body = JSON.stringify({
    mailbox: MAILBOX,
    fromName: 'Load Test',
    fromEmail: `customer+${__VU}@example.com`,
    subject: `Statement copy request ${n}`,
    body: 'Please send me a copy of my account statement for the last quarter.',
    messageId: `<load-${n}@example.com>`,
  });
  const ts = Math.floor(Date.now() / 1000);
  const sig = 'v1=' + crypto.hmac('sha256', SECRET, `${ts}.${body}`, 'hex');
  const r = http.post(`${BASE}/v1/intake/messages`, body, {
    headers: { 'content-type': 'application/json', 'x-ci-timestamp': `${ts}`, 'x-ci-signature': sig },
    tags: { name: 'intake' },
  });
  check(r, { 'intake accepted': (res) => res.status === 200 || res.status === 202 });
}

function login() {
  const email = STAFF[__VU % STAFF.length];
  const r = http.post(`${BASE}/v1/auth/login`, JSON.stringify({ email }), {
    headers: { 'content-type': 'application/json' },
    tags: { name: 'login' },
  });
  check(r, { 'signed in': (res) => res.status === 200 });
  return r.status === 200 ? r.json('csrfToken') : null;
}

let signedIn = false;

export function people() {
  if (!signedIn) {
    signedIn = login() !== null;
  }
  const get = (path, name) => {
    const r = http.get(`${BASE}${path}`, { tags: { name } });
    check(r, { [`${name} ok`]: (res) => res.status === 200 });
    return r;
  };
  const inbox = get('/v1/inbox?filter=all', 'inbox');
  get('/v1/tickets?view=list', 'tickets');
  const first = inbox.status === 200 ? inbox.json('0.id') : null;
  if (first) get(`/v1/tickets/${first}`, 'ticket');
  if (__ITER % 5 === 0) get('/v1/insights/monitoring', 'monitoring');
  sleep(2 + Math.random() * 3); // a person reads between clicks
}

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { invalidate, keys } from './queries';

/**
 * Subscribe to the server's committed-event stream and invalidate what changed. EventSource reconnects
 * on its own; `connected` drives the "live" dot in the chrome.
 */
export function useLiveUpdates(enabled: boolean): boolean {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const es = new EventSource('/v1/stream', { withCredentials: true });
    let pending = new Set<string>();
    let flushTimer: ReturnType<typeof setTimeout> | undefined;
    const flush = () => {
      const topics = pending;
      pending = new Set();
      if (topics.has('ticket.updated') || topics.has('ticket.created') || topics.has('gate.updated')) {
        invalidate(qc, [keys.inboxAll, keys.ticketsAll, keys.ticketAll, keys.shift, keys.me]);
      }
      if (topics.has('activity.created')) invalidate(qc, [keys.activity]);
      if (topics.has('notification.created')) invalidate(qc, [keys.learning, keys.me]);
      if (topics.has('setup.updated')) invalidate(qc, [keys.boards, keys.agents, keys.actions, keys.policies, keys.knowledge, keys.taxonomy, keys.admin]);
      if (topics.has('people.updated')) invalidate(qc, [keys.people, keys.performance]);
    };
    const on = (topic: string) => () => {
      pending.add(topic);
      clearTimeout(flushTimer);
      flushTimer = setTimeout(flush, 150); // coalesce bursts (one approval emits several events)
    };
    const topics = ['ticket.updated', 'ticket.created', 'gate.updated', 'activity.created', 'notification.created', 'setup.updated', 'people.updated'];
    for (const t of topics) es.addEventListener(t, on(t));
    es.addEventListener('ready', () => setConnected(true));
    es.onerror = () => setConnected(false);
    return () => {
      clearTimeout(flushTimer);
      es.close();
      setConnected(false);
    };
  }, [enabled, qc]);
  return connected;
}

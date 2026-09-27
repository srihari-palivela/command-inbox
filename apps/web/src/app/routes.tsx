import type { Capability, NavCounts } from '@ci/contracts';
import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

export interface ScreenDef {
  key: string;
  path: string;
  label: string;
  meta: string;
  cap: Capability | null;
  badge?: (n: NavCounts) => number | undefined;
  hot?: boolean;
  component: LazyExoticComponent<ComponentType>;
}

export interface NavGroup {
  label: string;
  items: ScreenDef[];
}

const screen = (def: Omit<ScreenDef, 'component'>, load: () => Promise<{ default: ComponentType }>): ScreenDef => ({ ...def, component: lazy(load) });

export const SCREENS = {
  inbox: screen({ key: 'inbox', path: '/inbox', label: 'Inbox', meta: 'your queue', cap: 'ticket.work', badge: (n) => n.inbox }, () => import('../features/inbox/InboxScreen')),
  tickets: screen({ key: 'tickets', path: '/tickets', label: 'Tickets', meta: 'every ticket, by status', cap: 'ticket.work', badge: (n) => n.tickets }, () => import('../features/tickets/TicketsScreen')),
  boards: screen({ key: 'boards', path: '/boards', label: 'Boards', meta: 'mail sources and their agents', cap: 'ticket.work', badge: (n) => n.boards }, () => import('../features/boards/BoardsScreen')),
  performance: screen({ key: 'performance', path: '/performance', label: 'Performance', meta: 'speed and deadlines', cap: 'insights.view', badge: (n) => n.alerts || undefined, hot: true }, () => import('../features/insights/PerformanceScreen')),
  results: screen({ key: 'results', path: '/results', label: 'Results', meta: 'before and after', cap: 'insights.view' }, () => import('../features/insights/ResultsScreen')),
  people: screen({ key: 'people', path: '/people', label: 'Skills & clearance', meta: 'who is cleared for what', cap: 'ticket.work' }, () => import('../features/people/PeopleScreen')),
  learning: screen({ key: 'learning', path: '/learning', label: 'Learning', meta: 'courses and updates', cap: null, badge: (n) => n.learning || undefined, hot: true }, () => import('../features/learning/LearningScreen')),
  agents: screen({ key: 'agents', path: '/setup/agents', label: 'AI agents', meta: 'prompts, models, evals', cap: 'setup.view', badge: (n) => n.agents }, () => import('../features/setup/AgentsScreen')),
  actions: screen({ key: 'actions', path: '/setup/actions', label: 'What it can do', meta: 'actions and risk groups', cap: 'setup.view' }, () => import('../features/setup/ActionsScreen')),
  policies: screen({ key: 'policies', path: '/setup/policies', label: 'Rules & policies', meta: 'bucketing, priority, approvals', cap: 'setup.view' }, () => import('../features/setup/PoliciesScreen')),
  knowledge: screen({ key: 'knowledge', path: '/setup/knowledge', label: 'What it knows', meta: 'sources and gaps', cap: 'setup.view', badge: (n) => n.gaps }, () => import('../features/setup/KnowledgeScreen')),
  ownership: screen({ key: 'ownership', path: '/setup/ownership', label: 'Who owns what', meta: 'query types by team', cap: 'setup.view' }, () => import('../features/setup/OwnershipScreen')),
  mailboxes: screen({ key: 'mailboxes', path: '/setup/mailboxes', label: 'Where mail arrives', meta: 'mailboxes and systems', cap: 'setup.view' }, () => import('../features/setup/MailboxesScreen')),
  settings: screen({ key: 'settings', path: '/settings', label: 'Settings', meta: 'profile and preferences', cap: null }, () => import('../features/settings/SettingsScreen')),
} as const;

export const NAV: NavGroup[] = [
  { label: 'My work', items: [SCREENS.inbox, SCREENS.tickets, SCREENS.boards] },
  { label: 'How we are doing', items: [SCREENS.performance, SCREENS.results] },
  { label: 'People', items: [SCREENS.people, SCREENS.learning] },
  { label: 'Set up the AI', items: [SCREENS.agents, SCREENS.actions, SCREENS.policies, SCREENS.knowledge, SCREENS.ownership, SCREENS.mailboxes] },
];

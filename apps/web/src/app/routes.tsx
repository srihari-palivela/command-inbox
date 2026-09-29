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
  /** Extra nav condition on top of `cap` (the route itself stays reachable with `cap`). */
  visible?: (caps: ReadonlySet<Capability>) => boolean;
  component: LazyExoticComponent<ComponentType>;
}

/** Whether a screen belongs in this user's navigation and palette. */
export const canSee = (sc: ScreenDef, caps: ReadonlySet<Capability>) =>
  (!sc.cap || caps.has(sc.cap)) && (!sc.visible || sc.visible(caps));

export interface NavGroup {
  label: string;
  items: ScreenDef[];
}

const screen = (
  def: Omit<ScreenDef, 'component'>,
  load: () => Promise<{ default: ComponentType }>,
): ScreenDef => ({ ...def, component: lazy(load) });

export const SCREENS = {
  inbox: screen(
    {
      key: 'inbox',
      path: '/inbox',
      label: 'Inbox',
      meta: 'your queue',
      cap: 'ticket.work',
      badge: (n) => n.inbox,
    },
    () => import('../features/inbox/InboxScreen'),
  ),
  tickets: screen(
    {
      key: 'tickets',
      path: '/tickets',
      label: 'Tickets',
      meta: 'every ticket, by status',
      cap: 'ticket.work',
      badge: (n) => n.tickets,
    },
    () => import('../features/tickets/TicketsScreen'),
  ),
  boards: screen(
    {
      key: 'boards',
      path: '/boards',
      label: 'Boards',
      meta: 'mail sources and their agents',
      cap: 'ticket.work',
      badge: (n) => n.boards,
    },
    () => import('../features/boards/BoardsScreen'),
  ),
  performance: screen(
    {
      key: 'performance',
      path: '/performance',
      label: 'Performance',
      meta: 'speed and deadlines',
      cap: 'insights.view',
      badge: (n) => n.alerts || undefined,
      hot: true,
    },
    () => import('../features/insights/PerformanceScreen'),
  ),
  results: screen(
    { key: 'results', path: '/results', label: 'Results', meta: 'before and after', cap: 'insights.view' },
    () => import('../features/insights/ResultsScreen'),
  ),
  people: screen(
    {
      key: 'people',
      path: '/people',
      label: 'Skills & clearance',
      meta: 'who is cleared for what',
      cap: 'ticket.work',
    },
    () => import('../features/people/PeopleScreen'),
  ),
  learning: screen(
    {
      key: 'learning',
      path: '/learning',
      label: 'Learning',
      meta: 'courses and updates',
      cap: null,
      badge: (n) => n.learning || undefined,
      hot: true,
    },
    () => import('../features/learning/LearningScreen'),
  ),
  agents: screen(
    {
      key: 'agents',
      path: '/setup/agents',
      label: 'AI agents',
      meta: 'prompts, models, evals',
      cap: 'setup.view',
      badge: (n) => n.agents,
    },
    () => import('../features/setup/AgentsScreen'),
  ),
  actions: screen(
    {
      key: 'actions',
      path: '/setup/actions',
      label: 'What it can do',
      meta: 'actions and risk groups',
      cap: 'setup.view',
    },
    () => import('../features/setup/ActionsScreen'),
  ),
  policies: screen(
    {
      key: 'policies',
      path: '/setup/policies',
      label: 'Rules & policies',
      meta: 'bucketing, priority, approvals',
      cap: 'setup.view',
    },
    () => import('../features/setup/PoliciesScreen'),
  ),
  knowledge: screen(
    {
      key: 'knowledge',
      path: '/setup/knowledge',
      label: 'What it knows',
      meta: 'sources and gaps',
      cap: 'setup.view',
      badge: (n) => n.gaps,
    },
    () => import('../features/setup/KnowledgeScreen'),
  ),
  ownership: screen(
    {
      key: 'ownership',
      path: '/setup/ownership',
      label: 'Who owns what',
      meta: 'query types by team',
      cap: 'setup.view',
    },
    () => import('../features/setup/OwnershipScreen'),
  ),
  mailboxes: screen(
    {
      key: 'mailboxes',
      path: '/setup/mailboxes',
      label: 'Where mail arrives',
      meta: 'mailboxes and systems',
      cap: 'setup.view',
    },
    () => import('../features/setup/MailboxesScreen'),
  ),
  deployments: screen(
    {
      key: 'deployments',
      path: '/admin/deployments',
      label: 'Deployments',
      meta: 'versions, rollout, mailboxes',
      cap: 'deployment.view',
      // Every role may look at deployments (a ticket names the version that triaged it), but the admin
      // area is for people who change them or judge them: view-only users reach it by link, not by nav.
      visible: (c) => c.has('deployment.edit') || c.has('deployment.publish') || c.has('evals.view'),
    },
    () => import('../features/admin/DeploymentsScreen'),
  ),
  evals: screen(
    {
      key: 'evals',
      path: '/admin/evals',
      label: 'Evals',
      meta: 'datasets, runs, gates',
      cap: 'evals.view',
    },
    () => import('../features/admin/EvalsScreen'),
  ),
  onboarding: screen(
    {
      key: 'onboarding',
      path: '/onboarding',
      label: 'Getting started',
      meta: 'steps to go live',
      cap: 'workspace.manage',
    },
    () => import('../features/admin/OnboardingScreen'),
  ),
  organisation: screen(
    {
      key: 'organisation',
      path: '/admin/organisation',
      label: 'Organisation',
      meta: 'profile and single sign-on',
      cap: 'workspace.manage',
    },
    () => import('../features/admin/OrganisationScreen'),
  ),
  members: screen(
    {
      key: 'members',
      path: '/admin/members',
      label: 'Members',
      meta: 'roles and invitations',
      cap: 'members.manage',
    },
    () => import('../features/admin/MembersScreen'),
  ),
  permissions: screen(
    {
      key: 'permissions',
      path: '/admin/permissions',
      label: 'Permissions',
      meta: 'what staff and team leads may do',
      cap: 'rbac.manage',
    },
    () => import('../features/admin/PermissionsScreen'),
  ),
  settings: screen(
    { key: 'settings', path: '/settings', label: 'Settings', meta: 'profile and preferences', cap: null },
    () => import('../features/settings/SettingsScreen'),
  ),
} as const;

/** Deep-linkable detail pages that sit under a screen (not in the nav). Paths are relative to `/`. */
export const DETAIL_ROUTES: { path: string; component: LazyExoticComponent<ComponentType> }[] = [
  {
    path: 'admin/deployments/:deploymentId',
    component: lazy(() => import('../features/admin/DeploymentDetailScreen')),
  },
  {
    path: 'admin/evals/datasets/:datasetId',
    component: lazy(() => import('../features/admin/EvalDatasetScreen')),
  },
  { path: 'admin/evals/runs/:runId', component: lazy(() => import('../features/admin/EvalRunScreen')) },
];

export const NAV: NavGroup[] = [
  { label: 'My work', items: [SCREENS.inbox, SCREENS.tickets, SCREENS.boards] },
  { label: 'How we are doing', items: [SCREENS.performance, SCREENS.results] },
  { label: 'People', items: [SCREENS.people, SCREENS.learning] },
  {
    label: 'Set up the AI',
    items: [
      SCREENS.agents,
      SCREENS.actions,
      SCREENS.policies,
      SCREENS.knowledge,
      SCREENS.ownership,
      SCREENS.mailboxes,
    ],
  },
  {
    label: 'Administration',
    items: [
      SCREENS.onboarding,
      SCREENS.organisation,
      SCREENS.deployments,
      SCREENS.evals,
      SCREENS.members,
      SCREENS.permissions,
    ],
  },
];

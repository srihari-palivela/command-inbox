/**
 * Server state. One query key per resource; the SSE stream invalidates keys when the server commits a
 * change, so every screen stays live without polling.
 */
import type {
  ActionsDTO,
  ActivityDTO,
  AdminDTO,
  AgentsOverviewDTO,
  AutoAssignResultDTO,
  BoardDTO,
  CallDTO,
  CopilotAnswerDTO,
  DemoUserDTO,
  InboxDTO,
  KnowledgeDTO,
  KnowledgeDocumentDetailDTO,
  KnowledgeDocumentDTO,
  LearningDTO,
  MeDTO,
  NlFilterDTO,
  OrgChoiceDTO,
  PeopleDTO,
  PerformanceDTO,
  PoliciesDTO,
  ResultsDTO,
  SearchResultDTO,
  SessionDTO,
  ShiftDTO,
  TaxonomyDTO,
  TicketDetailDTO,
  TicketFilters,
  TicketListDTO,
  AuditVerifyDTO,
  DeploymentDTO,
  DeploymentDetailDTO,
  EvalCaseDTO,
  EvalDatasetDTO,
  EvalResultDTO,
  EvalRunDTO,
  InvitationDTO,
  MemberDTO,
  PermissionMatrixDTO,
} from '@ci/contracts';
import { QueryClient, useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { api, ApiError, qs, setCsrfToken } from './api';
import { toast } from './toast';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
    mutations: {
      onError: (err) => {
        toast.error(err instanceof ApiError ? err.problem.title : 'Something went wrong. Try again.');
      },
    },
  },
});

export const keys = {
  me: ['me'] as const,
  onboarding: ['onboarding'] as const,
  mailboxConnections: ['mailbox-connections'] as const,
  workspaceProfile: ['workspace', 'profile'] as const,
  modelPolicy: ['workspace', 'model-policy'] as const,
  taxonomyAdmin: ['taxonomy', 'admin'] as const,
  slaPolicies: ['taxonomy', 'sla'] as const,
  demo: ['auth', 'demo'] as const,
  inbox: (filter: string) => ['inbox', filter] as const,
  inboxAll: ['inbox'] as const,
  tickets: (f: TicketFilters) => ['tickets', f] as const,
  ticketsAll: ['tickets'] as const,
  ticket: (id: string) => ['ticket', id] as const,
  ticketAll: ['ticket'] as const,
  activity: ['activity'] as const,
  shift: ['shift'] as const,
  call: (id: string) => ['call', id] as const,
  people: ['people'] as const,
  performance: ['insights', 'performance'] as const,
  results: ['insights', 'results'] as const,
  monitoring: (days: number) => ['insights', 'monitoring', days] as const,
  learning: ['learning'] as const,
  boards: ['boards'] as const,
  agents: ['agents'] as const,
  actions: ['actions'] as const,
  policies: ['policies'] as const,
  knowledge: ['knowledge'] as const,
  knowledgeDocs: ['knowledge', 'documents'] as const,
  knowledgeDoc: (id: string) => ['knowledge', 'documents', id] as const,
  taxonomy: ['taxonomy'] as const,
  admin: ['admin'] as const,
  sessions: ['sessions'] as const,
  search: (q: string) => ['search', q] as const,
  // Tenant administration
  deployments: ['deployments'] as const,
  deployment: (id: string) => ['deployments', id] as const,
  evals: ['evals'] as const,
  evalDatasets: (deploymentId: string) => ['evals', 'datasets', deploymentId] as const,
  evalDataset: (id: string) => ['evals', 'dataset', id] as const,
  evalCases: (datasetId: string) => ['evals', 'cases', datasetId] as const,
  evalRuns: (f: { deploymentId?: string; versionId?: string }) => ['evals', 'runs', f] as const,
  evalRun: (id: string) => ['evals', 'run', id] as const,
  evalResults: (id: string) => ['evals', 'results', id] as const,
  members: ['members'] as const,
  invitations: ['invitations'] as const,
  permissions: ['permissions'] as const,
};

// ── Session ───────────────────────────────────────────────────────────────────
export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => {
      try {
        const me = await api.get<MeDTO>('/v1/me');
        setCsrfToken(me.csrfToken);
        return me;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    staleTime: 30_000,
  });
}

export function useDemo() {
  return useQuery({
    queryKey: keys.demo,
    queryFn: () =>
      api.get<{ demoMode: boolean; sso: boolean; users: DemoUserDTO[]; orgs: OrgChoiceDTO[] }>(
        '/v1/auth/demo',
      ),
    staleTime: Infinity,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => api.post<MeDTO>('/v1/auth/login', { email }),
    onSuccess: (me) => {
      setCsrfToken(me.csrfToken);
      dropAllButMe(qc);
      qc.setQueryData(keys.me, me);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/v1/auth/logout'),
    onSettled: () => {
      dropAllButMe(qc);
      qc.setQueryData(keys.me, null);
    },
  });
}

/**
 * A new identity invalidates every cached answer. `me` itself is kept (and overwritten) rather than
 * removed: the App is observing it, and a removed query would leave that observer on stale data.
 */
function dropAllButMe(qc: QueryClient) {
  qc.removeQueries({ predicate: (q) => q.queryKey[0] !== keys.me[0] });
}

/** Switching workspace changes everything: drop the whole cache. */
export function useSessionSwitch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { orgId: string }) => api.post('/v1/session/org', { orgId: v.orgId }),
    onSuccess: async () => {
      dropAllButMe(qc);
      const me = await api.get<MeDTO>('/v1/me');
      setCsrfToken(me.csrfToken);
      qc.setQueryData(keys.me, me);
    },
  });
}

// ── Tickets ───────────────────────────────────────────────────────────────────
export const useInbox = (filter: string) =>
  useQuery({ queryKey: keys.inbox(filter), queryFn: () => api.get<InboxDTO>(`/v1/inbox${qs({ filter })}`) });

export const useTickets = (filters: TicketFilters) =>
  useQuery({
    queryKey: keys.tickets(filters),
    queryFn: () => api.get<TicketListDTO>(`/v1/tickets${qs(filters as Record<string, string>)}`),
    placeholderData: (prev) => prev,
  });

export const useTicket = (id: string | null | undefined) =>
  useQuery({
    queryKey: keys.ticket(id ?? ''),
    queryFn: () => api.get<TicketDetailDTO>(`/v1/tickets/${id}`),
    enabled: !!id,
  });

export const useActivity = () =>
  useQuery({ queryKey: keys.activity, queryFn: () => api.get<ActivityDTO[]>('/v1/activity') });
export const useShift = () =>
  useQuery({ queryKey: keys.shift, queryFn: () => api.get<ShiftDTO>('/v1/shift') });

export const useCall = (id: string | null) =>
  useQuery({
    queryKey: keys.call(id ?? ''),
    queryFn: () => api.get<CallDTO>(`/v1/calls/${id}`),
    enabled: !!id,
    refetchInterval: (q) =>
      q.state.data && (q.state.data.state === 'live' || q.state.data.state === 'dialing') ? 900 : false,
  });

export const useNlFilter = () =>
  useMutation({ mutationFn: (query: string) => api.post<NlFilterDTO>('/v1/tickets/nl-filter', { query }) });

// ── Workspace ─────────────────────────────────────────────────────────────────
export const usePeople = () =>
  useQuery({ queryKey: keys.people, queryFn: () => api.get<PeopleDTO>('/v1/people') });
export const usePerformance = () =>
  useQuery({
    queryKey: keys.performance,
    queryFn: () => api.get<PerformanceDTO>('/v1/insights/performance'),
  });
export const useResults = () =>
  useQuery({ queryKey: keys.results, queryFn: () => api.get<ResultsDTO>('/v1/insights/results') });
export const useLearning = () =>
  useQuery({ queryKey: keys.learning, queryFn: () => api.get<LearningDTO>('/v1/learning') });
export const useBoards = () =>
  useQuery({ queryKey: keys.boards, queryFn: () => api.get<BoardDTO[]>('/v1/boards') });
export const useAgents = () =>
  useQuery({ queryKey: keys.agents, queryFn: () => api.get<AgentsOverviewDTO>('/v1/agents') });
export const useActions = () =>
  useQuery({ queryKey: keys.actions, queryFn: () => api.get<ActionsDTO>('/v1/actions') });
export const usePolicies = () =>
  useQuery({ queryKey: keys.policies, queryFn: () => api.get<PoliciesDTO>('/v1/policies') });
export const useKnowledge = () =>
  useQuery({ queryKey: keys.knowledge, queryFn: () => api.get<KnowledgeDTO>('/v1/knowledge') });
/** Documents still being scanned or parsed are polled until they settle. */
const IN_FLIGHT = new Set(['queued', 'scanning', 'parsing']);
export const useKnowledgeDocuments = () =>
  useQuery({
    queryKey: keys.knowledgeDocs,
    queryFn: () => api.get<KnowledgeDocumentDTO[]>('/v1/knowledge/documents'),
    refetchInterval: (q) => (q.state.data?.some((d) => IN_FLIGHT.has(d.parseStatus)) ? 2500 : false),
  });
export const useKnowledgeDocument = (id: string | null) =>
  useQuery({
    queryKey: keys.knowledgeDoc(id ?? ''),
    queryFn: () => api.get<KnowledgeDocumentDetailDTO>(`/v1/knowledge/documents/${id}`),
    enabled: !!id,
  });
export const useTaxonomy = () =>
  useQuery({ queryKey: keys.taxonomy, queryFn: () => api.get<TaxonomyDTO>('/v1/taxonomy') });
export const useAdmin = () =>
  useQuery({ queryKey: keys.admin, queryFn: () => api.get<AdminDTO>('/v1/admin') });
export const useSessions = () =>
  useQuery({ queryKey: keys.sessions, queryFn: () => api.get<SessionDTO[]>('/v1/sessions') });
export const useAuditVerify = (enabled: boolean) =>
  useQuery({
    queryKey: ['audit', 'verify'],
    queryFn: () => api.get<AuditVerifyDTO>('/v1/audit/verify'),
    enabled,
  });

// ── Tenant administration ─────────────────────────────────────────────────────
export const useDeployments = (enabled = true) =>
  useQuery({
    queryKey: keys.deployments,
    queryFn: () => api.get<DeploymentDTO[]>('/v1/deployments'),
    enabled,
  });
export const useDeployment = (id: string | null | undefined) =>
  useQuery({
    queryKey: keys.deployment(id ?? ''),
    queryFn: () => api.get<DeploymentDetailDTO>(`/v1/deployments/${id}`),
    enabled: !!id,
  });
export const useEvalDatasets = (deploymentId: string | null | undefined) =>
  useQuery({
    queryKey: keys.evalDatasets(deploymentId ?? ''),
    queryFn: () => api.get<EvalDatasetDTO[]>(`/v1/evals/datasets${qs({ deploymentId })}`),
    enabled: !!deploymentId,
  });
export const useEvalDataset = (id: string | null | undefined) =>
  useQuery({
    queryKey: keys.evalDataset(id ?? ''),
    queryFn: () => api.get<EvalDatasetDTO>(`/v1/evals/datasets/${id}`),
    enabled: !!id,
  });
export const useEvalCases = (datasetId: string | null | undefined) =>
  useQuery({
    queryKey: keys.evalCases(datasetId ?? ''),
    queryFn: () => api.get<EvalCaseDTO[]>(`/v1/evals/datasets/${datasetId}/cases`),
    enabled: !!datasetId,
  });
/** A queued or running eval is also polled slowly, in case the live stream drops the `eval.updated` event. */
const pending = (state: string | undefined) => state === 'queued' || state === 'running';
export const useEvalRuns = (f: { deploymentId?: string; versionId?: string }, enabled = true) =>
  useQuery({
    queryKey: keys.evalRuns(f),
    queryFn: () => api.get<EvalRunDTO[]>(`/v1/evals/runs${qs(f)}`),
    enabled: enabled && !!(f.deploymentId || f.versionId),
    refetchInterval: (q) => (q.state.data?.some((r) => pending(r.state)) ? 4000 : false),
  });
export const useEvalRun = (id: string | null | undefined) =>
  useQuery({
    queryKey: keys.evalRun(id ?? ''),
    queryFn: () => api.get<EvalRunDTO>(`/v1/evals/runs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (pending(q.state.data?.state) ? 4000 : false),
  });
export const useEvalResults = (id: string | null | undefined, enabled = true) =>
  useQuery({
    queryKey: keys.evalResults(id ?? ''),
    queryFn: () => api.get<EvalResultDTO[]>(`/v1/evals/runs/${id}/results`),
    enabled: enabled && !!id,
  });
export const useMembers = () =>
  useQuery({ queryKey: keys.members, queryFn: () => api.get<MemberDTO[]>('/v1/members') });
export const useInvitations = () =>
  useQuery({ queryKey: keys.invitations, queryFn: () => api.get<InvitationDTO[]>('/v1/invitations') });
export const usePermissions = () =>
  useQuery({
    queryKey: keys.permissions,
    queryFn: () => api.get<PermissionMatrixDTO>('/v1/permissions'),
  });

export const useSearch = (q: string) =>
  useQuery({
    queryKey: keys.search(q),
    queryFn: () => api.get<SearchResultDTO>(`/v1/search${qs({ q })}`),
    enabled: q.trim().length >= 2,
    placeholderData: (prev) => prev,
  });

export const useAsk = () =>
  useMutation({
    mutationFn: (question: string) => api.post<CopilotAnswerDTO>('/v1/copilot/ask', { question }),
  });
export const useAutoAssign = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AutoAssignResultDTO>('/v1/assignments/auto'),
    onSuccess: () =>
      invalidate(qc, [keys.ticketsAll, keys.inboxAll, keys.people, keys.performance, keys.activity]),
  });
};

/**
 * A mutation that invalidates the given keys on success and optionally toasts. Screens use this for
 * every write, so a successful change always refreshes what it touched.
 */
export function useAction<TVars, TResult = unknown>(
  fn: (vars: TVars) => Promise<TResult>,
  opts: { invalidate?: QueryKey[]; success?: string | ((r: TResult, v: TVars) => string | null) } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r, v) => {
      invalidate(qc, opts.invalidate ?? []);
      const msg = typeof opts.success === 'function' ? opts.success(r, v) : opts.success;
      if (msg) toast.show(msg);
    },
  });
}

export function invalidate(qc: QueryClient, list: QueryKey[]) {
  for (const k of list) void qc.invalidateQueries({ queryKey: k });
}

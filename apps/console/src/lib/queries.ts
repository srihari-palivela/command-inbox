/** Server state for the console. Screens poll only while something is in flight (provisioning). */
import type {
  CreateTenantBody,
  PlatformAuditEventDTO,
  PlatformAuditVerifyDTO,
  PlatformAuthConfigDTO,
  PlatformCapability,
  PlatformMeDTO,
  ReinviteBody,
  TenantDetailDTO,
  TenantSummaryDTO,
} from '@ci/contracts';
import { toast } from '@web/lib/toast';
import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, qs, setCsrfToken } from './api';
import { TENANT_STATUS } from './presentation';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

export const keys = {
  me: ['me'] as const,
  config: ['auth', 'config'] as const,
  tenants: ['tenants'] as const,
  tenant: (id: string) => ['tenant', id] as const,
  audit: (tenantId?: string) => ['audit', tenantId ?? 'all'] as const,
  auditAll: ['audit'] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => {
      try {
        const me = await api.get<PlatformMeDTO>('/v1/platform/me');
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

/** A capability check for the signed-in operator; the server enforces the same rule on every call. */
export function useCan() {
  const caps = new Set<PlatformCapability>(useMe().data?.operator.capabilities ?? []);
  return (c: PlatformCapability) => caps.has(c);
}

export const useAuthConfig = () =>
  useQuery({
    queryKey: keys.config,
    queryFn: () => api.get<PlatformAuthConfigDTO>('/v1/platform/auth/config'),
    staleTime: Infinity,
  });

/** A new identity invalidates every cached answer except `me`, which is overwritten in place. */
function resetIdentity(qc: QueryClient, me: PlatformMeDTO | null) {
  qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' && q.queryKey[0] !== 'auth' });
  qc.setQueryData(keys.me, me);
}

export function useDevLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => api.post<PlatformMeDTO>('/v1/platform/auth/dev-login', { email }),
    onSuccess: (me) => {
      setCsrfToken(me.csrfToken);
      resetIdentity(qc, me);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/v1/platform/auth/logout'),
    onSettled: () => {
      setCsrfToken('');
      resetIdentity(qc, null);
    },
  });
}

export const useTenants = () =>
  useQuery({
    queryKey: keys.tenants,
    queryFn: () => api.get<TenantSummaryDTO[]>('/v1/platform/tenants'),
    refetchInterval: (q) =>
      q.state.data?.some((t) => t.status === 'provisioning' && t.provisioning !== 'failed') ? 5_000 : false,
  });

export const useTenant = (id: string) =>
  useQuery({
    queryKey: keys.tenant(id),
    queryFn: () => api.get<TenantDetailDTO>(`/v1/platform/tenants/${id}`),
    refetchInterval: (q) =>
      q.state.data?.status === 'provisioning' && q.state.data.provisioning !== 'failed' ? 2_000 : false,
  });

export const useAudit = (tenantId?: string, limit = 200) =>
  useQuery({
    queryKey: keys.audit(tenantId),
    queryFn: () => api.get<PlatformAuditEventDTO[]>(`/v1/platform/audit${qs({ tenantId, limit })}`),
  });

export function useAuditVerify() {
  return useMutation({
    mutationFn: () => api.get<PlatformAuditVerifyDTO>('/v1/platform/audit/verify'),
    onError: () => undefined,
  });
}

/** Every tenant write answers with the fresh detail: seed its cache and refresh the list and audit. */
function useTenantWrite<TVars>(
  fn: (vars: TVars) => Promise<TenantDetailDTO>,
  success?: (t: TenantDetailDTO) => string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (t) => {
      qc.setQueryData(keys.tenant(t.id), t);
      void qc.invalidateQueries({ queryKey: keys.tenants });
      void qc.invalidateQueries({ queryKey: keys.auditAll });
      if (success) toast.show(success(t));
    },
  });
}

export const useCreateTenant = () =>
  useTenantWrite(
    (body: CreateTenantBody) => api.post<TenantDetailDTO>('/v1/platform/tenants', body),
    (t) =>
      t.status === 'draft' ? `Created ${t.name} as a draft.` : `Created ${t.name}. Provisioning has started.`,
  );

export type LifecycleAction = 'provision' | 'suspend' | 'resume' | 'archive';

export const useLifecycle = (id: string) =>
  useTenantWrite(
    ({ action, reason }: { action: LifecycleAction; reason?: string }) =>
      api.post<TenantDetailDTO>(
        `/v1/platform/tenants/${id}/${action}`,
        action === 'provision' ? undefined : { reason },
      ),
    (t) => `${t.name} is now ${TENANT_STATUS[t.status].label.toLowerCase()}.`,
  );

export const useReinvite = (id: string) =>
  useTenantWrite(
    (body: ReinviteBody) => api.post<TenantDetailDTO>(`/v1/platform/tenants/${id}/invitations`, body),
    () => 'Invitation sent.',
  );

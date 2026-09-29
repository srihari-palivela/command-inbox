import { ErrorState, Skeleton, Toaster } from '@web/ui';
import { QueryClientProvider } from '@tanstack/react-query';
import { lazy } from 'react';
import { createBrowserRouter, Navigate, RouterProvider, useRouteError } from 'react-router-dom';
import { queryClient, useMe } from '../lib/queries';
import { Shell } from './Shell';
import { SignIn } from './SignIn';

const TenantsScreen = lazy(() => import('../features/tenants/TenantsScreen'));
const NewTenantScreen = lazy(() => import('../features/tenants/NewTenantScreen'));
const TenantDetailScreen = lazy(() => import('../features/tenants/TenantDetailScreen'));
const AuditScreen = lazy(() => import('../features/audit/AuditScreen'));

function Gate() {
  const me = useMe();
  if (me.isLoading)
    return (
      <div style={{ padding: 40, display: 'grid', gap: 12 }}>
        <Skeleton h={24} w="20%" />
        <Skeleton h={200} />
      </div>
    );
  if (me.error)
    return (
      <div style={{ padding: 40 }}>
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      </div>
    );
  if (!me.data) return <SignIn />;
  return <Shell me={me.data} />;
}

function RouteError() {
  const err = useRouteError();
  return (
    <div style={{ padding: 40 }}>
      <ErrorState
        error={err instanceof Error ? err : new Error('This page failed to load.')}
        onRetry={() => window.location.reload()}
      />
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <Gate />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/tenants" replace /> },
      { path: 'tenants', element: <TenantsScreen /> },
      { path: 'tenants/new', element: <NewTenantScreen /> },
      { path: 'tenants/:id', element: <TenantDetailScreen /> },
      { path: 'audit', element: <AuditScreen /> },
      { path: '*', element: <Navigate to="/tenants" replace /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  );
}

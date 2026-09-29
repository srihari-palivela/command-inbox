import { QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, Navigate, RouterProvider, useRouteError } from 'react-router-dom';
import { setTenantLocale } from '../lib/format';
import { queryClient, useMe } from '../lib/queries';
import { ErrorState, Skeleton, Toaster } from '../ui';
import { DETAIL_ROUTES, SCREENS } from './routes';
import { Shell } from './Shell';
import { AcceptInvitation } from './AcceptInvitation';
import { SignIn } from './SignIn';
import { UiProvider } from './ui-context';

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
  // Every formatter reads the workspace's locale, currency and time zone (re-set on a workspace switch).
  setTenantLocale(me.data.org);
  return <Shell me={me.data} />;
}

/** Until go-live an admin lands on the onboarding checklist; everyone else (and admins after) on the inbox. */
function Home() {
  const me = useMe().data;
  const onboarding =
    !!me?.capabilities.includes('workspace.manage') &&
    ['provisioned', 'onboarding', 'shadow'].includes(me.org.status);
  return <Navigate to={onboarding ? '/onboarding' : '/inbox'} replace />;
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

const screenRoutes = Object.values(SCREENS).map((sc) => {
  const C = sc.component;
  return { path: sc.path.slice(1), element: <C /> };
});

const detailRoutes = DETAIL_ROUTES.map(({ path, component: C }) => ({ path, element: <C /> }));

const router = createBrowserRouter([
  { path: '/accept', element: <AcceptInvitation />, errorElement: <RouteError /> },
  {
    path: '/',
    element: <Gate />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Home /> },
      ...screenRoutes,
      ...detailRoutes,
      { path: 'admin', element: <Navigate to="/admin/deployments" replace /> },
      {
        path: 'inbox/:ticket',
        element: (() => {
          const C = SCREENS.inbox.component;
          return <C />;
        })(),
      },
      { path: '*', element: <Navigate to="/inbox" replace /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <UiProvider>
        <RouterProvider router={router} />
        <Toaster />
      </UiProvider>
    </QueryClientProvider>
  );
}

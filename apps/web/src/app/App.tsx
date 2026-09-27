import { QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, Navigate, RouterProvider, useRouteError } from 'react-router-dom';
import { queryClient, useMe } from '../lib/queries';
import { ErrorState, Skeleton, Toaster } from '../ui';
import { SCREENS } from './routes';
import { Shell } from './Shell';
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

const screenRoutes = Object.values(SCREENS).map((sc) => {
  const C = sc.component;
  return { path: sc.path.slice(1), element: <C /> };
});

const router = createBrowserRouter([
  {
    path: '/',
    element: <Gate />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/inbox" replace /> },
      ...screenRoutes,
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

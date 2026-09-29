import type { PlatformMeDTO } from '@ci/contracts';
import s from '@web/app/Shell.module.css';
import { Button, Skeleton } from '@web/ui';
import { Suspense } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { OPERATOR_ROLE } from '../lib/presentation';
import { useLogout } from '../lib/queries';
import c from './Shell.module.css';

/** The web app's shell layout, trimmed to what an operator needs: tenants and the platform audit log. */
export function Shell({ me }: { me: PlatformMeDTO }) {
  const navigate = useNavigate();
  const logout = useLogout();
  const caps = new Set(me.operator.capabilities);
  const nav = [
    { to: '/tenants', label: 'Tenants', show: caps.has('tenants.view') },
    { to: '/fleet', label: 'Fleet health', show: caps.has('tenants.view') },
    { to: '/audit', label: 'Audit log', show: caps.has('audit.view') },
  ].filter((i) => i.show);

  return (
    <div className={s.root}>
      <a href="#main" className={s.skip}>
        Skip to content
      </a>
      <header className={s.header}>
        <div className={s.brand}>
          <div className={s.logo} aria-hidden>
            <div className={s.logoCore} />
          </div>
          <span className={`${s.brandName} ${c.brandName}`}>
            Command Inbox <span style={{ color: 'var(--muted)', fontWeight: 500 }}>· Platform console</span>
          </span>
        </div>
        <div className={s.right} style={{ marginLeft: 'auto' }}>
          <div style={{ textAlign: 'right', lineHeight: 1.3 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>{me.operator.name}</div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>
              {OPERATOR_ROLE[me.operator.role]} · {me.operator.email}
            </div>
          </div>
          <Button
            size="sm"
            variant="ghost"
            loading={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/') })}
          >
            Sign out
          </Button>
        </div>
      </header>

      <div className={s.body}>
        <nav className={`${s.nav} ${c.nav}`} aria-label="Main">
          <div className={s.group}>
            <div className={`${s.groupLabel} ${c.groupLabel}`}>Platform</div>
            {nav.map((i) => (
              <NavLink key={i.to} to={i.to} className={`${s.navItem} ${c.item}`} title={i.label}>
                <span className={s.mark} aria-hidden />
                <span className={`${s.navLabel} ${c.label}`}>{i.label}</span>
              </NavLink>
            ))}
          </div>
        </nav>

        <main id="main" className={s.main} tabIndex={-1}>
          <Suspense
            fallback={
              <div style={{ padding: 32, display: 'grid', gap: 12 }}>
                <Skeleton h={26} w="30%" />
                <Skeleton h={160} />
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}

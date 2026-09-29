import type { MeDTO } from '@ci/contracts';
import { Suspense, useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useLiveUpdates } from '../lib/live';
import { useLogout, useSessionSwitch } from '../lib/queries';
import { TEAM_ROLE_LABEL } from '../lib/presentation';
import { toast } from '../lib/toast';
import { MenuItem, Popover, Skeleton } from '../ui';
import { CommandPalette } from './CommandPalette';
import { NotificationCenter } from './NotificationCenter';
import { canSee, NAV } from './routes';
import s from './Shell.module.css';
import { useUi } from './ui-context';
import { ComposeUpdate } from '../features/learning/ComposeUpdate';
import { LearnOverlay } from '../features/learning/LearnOverlay';

export function Shell({ me }: { me: MeDTO }) {
  const ui = useUi();
  const navigate = useNavigate();
  const live = useLiveUpdates(true);
  const switcher = useSessionSwitch();
  const logout = useLogout();
  const [menu, setMenu] = useState<'org' | 'avatar' | 'notif' | null>(null);
  const caps = new Set(me.capabilities);

  // ⌘K / Ctrl+K opens the palette from anywhere; "?" is reserved for the shortcut sheet in the Inbox.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        ui.openPalette();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ui]);

  const switchOrg = (orgId: string, name: string) => {
    setMenu(null);
    switcher.mutate(
      { orgId },
      {
        onSuccess: () => {
          navigate('/inbox');
          toast.show(`Switched to ${name}.`);
        },
      },
    );
  };
  const workerTone =
    me.worker.state === 'degraded' || !live
      ? {
          fg: 'var(--warn)',
          bg: 'var(--warn-bg)',
          line: 'var(--warn-line)',
          dot: 'var(--warn-dot)',
          label: me.worker.state === 'degraded' ? 'Agent degraded' : 'Reconnecting',
        }
      : {
          fg: 'var(--ok)',
          bg: 'var(--ok-bg-2)',
          line: 'var(--ok-line)',
          dot: 'var(--ok-dot)',
          label: 'Agent live',
        };
  const unread = me.memberships.find((m) => m.org.id === me.org.id)?.unread ?? 0;

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
          <span className={s.brandName}>Command Inbox</span>
          <button
            type="button"
            className={s.orgBtn}
            onClick={() => setMenu(menu === 'org' ? null : 'org')}
            aria-haspopup="dialog"
            aria-expanded={menu === 'org'}
          >
            <span className={s.orgMark} style={{ background: me.org.bg, color: me.org.tint }}>
              {me.org.short}
            </span>
            <span className={s.orgName}>{me.org.name}</span>
            <span aria-hidden style={{ fontSize: 9, color: 'var(--muted)' }}>
              ▾
            </span>
          </button>
          <Popover
            open={menu === 'org'}
            onClose={() => setMenu(null)}
            label="Workspaces"
            style={{ top: 50, left: 14, width: 314 }}
          >
            <div className={s.popHead}>
              <div
                className="mono"
                style={{ fontSize: 9, fontWeight: 600, letterSpacing: '.13em', color: 'var(--muted-2)' }}
              >
                WORKSPACES
              </div>
            </div>
            <div style={{ padding: 6 }}>
              {me.memberships.map((m) => (
                <button
                  key={m.org.id}
                  type="button"
                  className={s.orgRow}
                  style={{ background: m.org.id === me.org.id ? 'var(--accent-bg-4)' : undefined }}
                  onClick={() => switchOrg(m.org.id, m.org.name)}
                >
                  <span
                    className={s.orgMark}
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: 8,
                      fontSize: 10.5,
                      background: m.org.bg,
                      color: m.org.tint,
                    }}
                  >
                    {m.org.short}
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600 }}>{m.org.name}</span>
                    <span style={{ display: 'block', fontSize: 11, color: 'var(--muted)' }}>
                      {TEAM_ROLE_LABEL[m.role]} · {m.org.plan} · {m.boards} boards · {m.people} people
                    </span>
                  </span>
                  {m.unread > 0 && m.org.id !== me.org.id && (
                    <span
                      className="mono"
                      style={{
                        fontSize: 10,
                        color: '#fff',
                        background: 'var(--bad)',
                        borderRadius: 10,
                        padding: '1px 6px',
                      }}
                    >
                      {m.unread}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <div className={s.popFoot}>Each workspace has its own boards, agents and audit log.</div>
          </Popover>
        </div>

        <div className={s.search}>
          <button
            type="button"
            className={s.searchBtn}
            onClick={ui.openPalette}
            aria-label="Search or ask the copilot (Command K)"
          >
            <span className={s.searchIcon} aria-hidden />
            <span
              style={{
                flex: 1,
                textAlign: 'left',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              Jump to a ticket, a customer, a policy, or ask the agent
            </span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--muted-2)' }}>
              ⌘K
            </span>
          </button>
        </div>

        <div className={s.right}>
          <button
            type="button"
            className={s.iconBtn}
            onClick={() => setMenu(menu === 'notif' ? null : 'notif')}
            aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
            aria-expanded={menu === 'notif'}
          >
            <svg width="14" height="15" viewBox="0 0 14 15" fill="none" aria-hidden>
              <path
                d="M7 1.5c-2.5 0-4 1.9-4 4.2v2.6L1.8 10.5c-.3.5 0 1.2.7 1.2h9c.7 0 1-.7.7-1.2L11 8.3V5.7c0-2.3-1.5-4.2-4-4.2z"
                stroke="#46484F"
                strokeWidth="1.3"
                strokeLinejoin="round"
              />
              <path
                d="M5.6 13.2c.3.6.8 1 1.4 1s1.1-.4 1.4-1"
                stroke="#46484F"
                strokeWidth="1.3"
                strokeLinecap="round"
              />
            </svg>
            {unread > 0 && <span className={s.badge}>{unread}</span>}
          </button>
          <NotificationCenter
            open={menu === 'notif'}
            onClose={() => setMenu(null)}
            canSend={caps.has('learning.send')}
          />
          <span
            className={s.live}
            style={{ color: workerTone.fg, background: workerTone.bg, borderColor: workerTone.line }}
            title={`Model provider: ${me.worker.provider}`}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: workerTone.dot,
                animation: 'breathe 1.8s ease-in-out infinite',
              }}
            />
            {workerTone.label}
          </span>
          <button
            type="button"
            className={s.avatarBtn}
            onClick={() => setMenu(menu === 'avatar' ? null : 'avatar')}
            aria-label={`Account: ${me.user.name}`}
            aria-expanded={menu === 'avatar'}
          >
            {me.user.initials}
          </button>
          <Popover
            open={menu === 'avatar'}
            onClose={() => setMenu(null)}
            label="Account"
            style={{ top: 42, right: 0, width: 262 }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '12px 13px',
                borderBottom: '1px solid var(--line-soft)',
              }}
            >
              <span className={s.avatarBtn} style={{ width: 32, height: 32, fontSize: 12 }}>
                {me.user.initials}
              </span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600 }}>{me.user.name}</div>
                <div
                  style={{
                    fontSize: 11,
                    color: 'var(--muted)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {TEAM_ROLE_LABEL[me.role]} · {me.user.email}
                </div>
              </div>
            </div>
            <div style={{ padding: 6 }} role="menu">
              <MenuItem
                onClick={() => {
                  setMenu(null);
                  navigate('/settings');
                }}
              >
                Settings
              </MenuItem>
              <MenuItem onClick={() => setMenu('org')}>Switch workspace</MenuItem>
              <MenuItem
                danger
                onClick={() => {
                  setMenu(null);
                  logout.mutate(undefined, { onSuccess: () => navigate('/') });
                }}
              >
                Sign out
              </MenuItem>
            </div>
          </Popover>
        </div>
      </header>

      <div className={s.body}>
        <nav className={s.nav} aria-label="Main">
          {NAV.map((g) => {
            const items = g.items.filter((i) => canSee(i, caps));
            if (!items.length) return null;
            return (
              <div className={s.group} key={g.label}>
                <div className={s.groupLabel}>{g.label}</div>
                {items.map((i) => {
                  const n = i.badge?.(me.nav);
                  return (
                    <NavLink key={i.key} to={i.path} className={s.navItem} title={i.label}>
                      <span className={s.mark} aria-hidden />
                      <span className={s.navLabel}>{i.label}</span>
                      {n !== undefined && (
                        <span className={`${s.navBadge} ${i.hot && n ? s.navBadgeHot : ''}`}>{n}</span>
                      )}
                    </NavLink>
                  );
                })}
              </div>
            );
          })}
          <div className={s.footerCard}>
            <div
              className="mono"
              style={{ fontSize: 9, fontWeight: 600, letterSpacing: '.12em', color: 'var(--muted)' }}
            >
              HOW MUCH THE AI DOES
            </div>
            <div className={s.bars} aria-hidden>
              {[0, 1, 2, 3].map((i) => (
                <span
                  key={i}
                  style={{
                    background:
                      i < me.nav.autonomousCells
                        ? 'var(--ok-dot)'
                        : i === me.nav.autonomousCells
                          ? 'var(--accent)'
                          : undefined,
                  }}
                />
              ))}
            </div>
            <div className={s.footerText}>
              {me.nav.autonomousCells} of 4 risk groups {me.nav.autonomousCells === 1 ? 'acts' : 'act'} on its
              own. Widening it needs Risk sign-off.
            </div>
            <div className={s.footerRule}>
              The AI addresses the query.
              <br />
              <b style={{ color: 'var(--ink)' }}>You stay accountable.</b>
            </div>
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

      <CommandPalette open={ui.paletteOpen} onClose={ui.closePalette} me={me} />
      <ComposeUpdate open={ui.composeOpen} onClose={ui.closeCompose} />
      <LearnOverlay courseId={ui.learnCourseId} onClose={ui.closeLearn} />
    </div>
  );
}

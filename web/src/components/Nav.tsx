'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';

const activeStyle = { color: 'var(--text-hover)', borderBottom: '2px solid var(--accent-red)' } as const;
const idleStyle = { color: 'var(--text-secondary)' } as const;

export function Nav() {
  const { user, loading, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [pendingUsers, setPendingUsers] = useState(0);
  const isAdmin = user?.role === 'ADMIN' && user.status === 'APPROVED';

  // How many sign-ins are waiting: one indexed COUNT per admin page load, and
  // again when /admin/users approves or denies someone. No polling.
  useEffect(() => {
    if (!isAdmin) { setPendingUsers(0); return; }
    const load = () => api.pendingUserCount().then((r) => setPendingUsers(r.count)).catch(() => {});
    load();
    window.addEventListener('users-changed', load);
    return () => window.removeEventListener('users-changed', load);
  }, [isAdmin]);
  const navRef = useRef<HTMLElement>(null);
  const pathname = usePathname();
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const isActive = (href: string) => pathname === href || (href !== '/' && pathname.startsWith(href));

  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) closeMenu();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeMenu(); };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen, closeMenu]);

  const signInHref = `/auth/callback?returnTo=${encodeURIComponent(pathname)}`;
  const displayName = user?.name || user?.email?.split('@')[0] || 'you';

  const authLinks = (onClick?: () => void) => (
    loading ? null : user ? (
      <>
        <Link href="/tokens" className="hover-text transition-colors" style={isActive('/tokens') ? activeStyle : idleStyle} onClick={onClick}>TOKENS</Link>
        {user.role === 'ADMIN' && (
          <>
            <Link href="/admin/agents" className="hover-text transition-colors" style={isActive('/admin/agents') ? activeStyle : { color: 'var(--accent-purple)' }} onClick={onClick}>AGENTS</Link>
            <Link href="/admin/users" className="hover-text transition-colors inline-flex items-center gap-1.5" style={isActive('/admin/users') ? activeStyle : { color: 'var(--accent-purple)' }} onClick={onClick}>
              USERS
              {pendingUsers > 0 && (
                <span className="text-[10px] font-bold px-1.5 rounded-full leading-4" title={`${pendingUsers} waiting for approval`} data-testid="pending-badge"
                  style={{ background: 'var(--accent-orange)', color: 'var(--accent-yellow-text)' }}>{pendingUsers}</span>
              )}
            </Link>
          </>
        )}
        <span style={{ color: 'var(--text-secondary)' }}>@{displayName}</span>
        <button onClick={() => { logout(); onClick?.(); }} className="hover-text transition-colors text-left" style={{ color: 'var(--text-muted)' }}>
          LOGOUT
        </button>
      </>
    ) : (
      <Link href={signInHref} className="hover-text transition-colors" style={{ color: 'var(--accent-green)' }} onClick={onClick}>
        SIGN IN
      </Link>
    )
  );

  return (
    <nav ref={navRef} className="sticky top-0 z-50 relative"
      style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
      <div className="flex items-center gap-6 px-6 py-3 max-w-full overflow-hidden">
        <Link href="/" aria-label="POPSMOSIS home" className="text-lg font-bold tracking-wider whitespace-nowrap shrink-0" style={{ color: 'var(--accent-red)' }}>
          POPSMOSIS
        </Link>

        {/* Desktop */}
        <div className="hidden lg:flex flex-wrap items-center gap-x-4 gap-y-1 text-sm flex-1">
          <Link href="/learn" className="hover-text transition-colors" style={isActive('/learn') ? activeStyle : idleStyle}>LEARN</Link>
          <span style={{ color: 'var(--border)' }}>|</span>
          {authLinks()}
        </div>

        {/* Hamburger (mobile) */}
        <button
          className="lg:hidden ml-auto flex flex-col justify-center items-center w-8 h-8 gap-[5px]"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Toggle menu"
          aria-expanded={menuOpen}
        >
          <span className="block w-5 h-[2px] rounded transition-all duration-200" style={{ backgroundColor: 'var(--text-secondary)', transform: menuOpen ? 'rotate(45deg) translate(4px, 4px)' : 'none' }} />
          <span className="block w-5 h-[2px] rounded transition-all duration-200" style={{ backgroundColor: 'var(--text-secondary)', opacity: menuOpen ? 0 : 1 }} />
          <span className="block w-5 h-[2px] rounded transition-all duration-200" style={{ backgroundColor: 'var(--text-secondary)', transform: menuOpen ? 'rotate(-45deg) translate(4px, -4px)' : 'none' }} />
        </button>
      </div>

      {menuOpen && (
        <div className="lg:hidden absolute top-full left-0 right-0 z-50 flex flex-col text-sm py-3 px-6 gap-3 animate-fade-in"
          style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
          <div className="flex flex-col gap-3" style={{ color: 'var(--text-secondary)' }}>
            <Link href="/learn" className="hover-text transition-colors" onClick={closeMenu}>LEARN</Link>
          </div>
          <div className="h-px" style={{ backgroundColor: 'var(--border)' }} />
          <div className="flex flex-col gap-3">{authLinks(closeMenu)}</div>
        </div>
      )}
    </nav>
  );
}

'use client';

import { createContext, useContext, useState, useCallback, useRef, useEffect, ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { ExclamationTriangleIcon, CheckCircleIcon, InformationCircleIcon } from '@heroicons/react/20/solid';
import { useAuth } from '@/lib/auth';

type ToastType = 'error' | 'success' | 'info';

interface Toast {
  id: number;
  message: string;
  type: ToastType;
  persistent?: boolean;
  key?: string;
}

interface ToastOptions {
  /** Stays up, with a close button, until the user closes it: for a message too long to read in a few seconds. */
  persistent?: boolean;
  /** Override the per-type auto-dismiss duration (ms). Ignored when persistent. */
  duration?: number;
  /** A toast with the same key replaces the one showing: the latest word on one thing, not a pile of them. */
  key?: string;
}

interface ToastContextValue {
  showToast: (message: string, type?: ToastType, options?: ToastOptions) => void;
}

const ToastCtx = createContext<ToastContextValue>({ showToast: () => {} });

export const useToast = () => useContext(ToastCtx);

let nextId = 0;
const MAX_TOASTS = 5;

const TOAST_CONFIG: Record<ToastType, { icon: typeof ExclamationTriangleIcon; color: string; duration: number }> = {
  error:   { icon: ExclamationTriangleIcon,  color: 'var(--accent-red)',    duration: 4000 },
  success: { icon: CheckCircleIcon,          color: 'var(--accent-green)',  duration: 3000 },
  info:    { icon: InformationCircleIcon,     color: 'var(--accent-blue)',   duration: 3000 },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  // Clear all pending timers on unmount
  useEffect(() => {
    const timers = timersRef.current;
    return () => { timers.forEach(t => clearTimeout(t)); };
  }, []);

  // A toast that stays is about the page it was shown on, for whoever was signed in: going to another page, or
  // signing out, closes it. One that times out is left be, as some are shown just before a page change.
  const pathname = usePathname();
  const userId = useAuth().user?.id ?? null;
  useEffect(() => {
    setToasts((prev: Toast[]) => (prev.some(t => t.persistent) ? prev.filter(t => !t.persistent) : prev));
  }, [pathname, userId]);

  const dismiss = useCallback((id: number) => {
    const timer = timersRef.current.get(id);
    if (timer) { clearTimeout(timer); timersRef.current.delete(id); }
    setToasts((prev: Toast[]) => prev.filter(t => t.id !== id));
  }, []);

  const showToast = useCallback((message: string, type: ToastType = 'info', options?: ToastOptions) => {
    const id = nextId++;
    const persistent = options?.persistent ?? false;
    const key = options?.key;
    setToasts((prev: Toast[]) => {
      // The one it replaces goes; its timer, if any, then finds nothing to remove.
      const kept = key ? prev.filter(t => t.key !== key) : prev;
      const next = [...kept, { id, message, type, persistent, key }];
      return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next;
    });
    if (!persistent) {
      const duration = options?.duration ?? TOAST_CONFIG[type].duration;
      const timer = setTimeout(() => {
        timersRef.current.delete(id);
        setToasts((prev: Toast[]) => prev.filter(t => t.id !== id));
      }, duration);
      timersRef.current.set(id, timer);
    }
  }, []);

  return (
    <ToastCtx.Provider value={{ showToast }}>
      {children}
      {toasts.length > 0 && (
        // Just under the nav (52px tall, 57px under 1024px wide) and beneath it (z-50), so a toast that stays never
        // covers the nav or its phone menu. Modal dialogs open in the top layer, over it either way. Across the page,
        // so a phone gives a toast its whole width (centred from the middle, it had half); the strip lets clicks
        // through to the page, and only the toasts themselves take them.
        <div role="status" aria-live="polite" data-testid="toasts" style={{ position: 'fixed', top: '4.25rem', left: '1rem', right: '1rem', zIndex: 40, display: 'flex', flexDirection: 'column', gap: '0.5rem', alignItems: 'center', pointerEvents: 'none' }}>
          {toasts.map(t => {
            const config = TOAST_CONFIG[t.type];
            const Icon = config.icon;
            return (
              <div
                key={t.id}
                style={{
                  background: 'var(--bg-secondary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  borderLeft: `3px solid ${config.color}`,
                  padding: '12px 20px',
                  borderRadius: '12px',
                  boxShadow: 'var(--toast-shadow)',
                  fontSize: '14px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  animation: 'toast-in 0.2s ease-out',
                  maxWidth: '40rem',
                  pointerEvents: 'auto',
                }}
              >
                <Icon style={{ width: '18px', height: '18px', flexShrink: 0, color: config.color }} />
                <span style={{ flex: 1 }}>{t.message}</span>
                {t.persistent && (
                  <button
                    onClick={() => dismiss(t.id)}
                    aria-label="Dismiss"
                    style={{
                      background: 'none', border: 'none', color: 'var(--text-muted)',
                      cursor: 'pointer', padding: 0, fontSize: '20px', lineHeight: 1,
                      flexShrink: 0, width: '32px', height: '32px', margin: '-6px -12px -6px 0',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}
                  >
                    &times;
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </ToastCtx.Provider>
  );
}

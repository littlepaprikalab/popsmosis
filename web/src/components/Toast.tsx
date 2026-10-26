'use client';

import { createContext, useContext, useState, useCallback, useRef, useEffect, ReactNode } from 'react';
import { ExclamationTriangleIcon, CheckCircleIcon, InformationCircleIcon } from '@heroicons/react/20/solid';

type ToastType = 'error' | 'success' | 'info';

interface Toast {
  id: number;
  message: string;
  type: ToastType;
  persistent?: boolean;
}

interface ToastOptions {
  persistent?: boolean;
  /** Override the per-type auto-dismiss duration (ms). Ignored when persistent. */
  duration?: number;
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

  const dismiss = useCallback((id: number) => {
    const timer = timersRef.current.get(id);
    if (timer) { clearTimeout(timer); timersRef.current.delete(id); }
    setToasts((prev: Toast[]) => prev.filter(t => t.id !== id));
  }, []);

  const showToast = useCallback((message: string, type: ToastType = 'info', options?: ToastOptions) => {
    const id = nextId++;
    const persistent = options?.persistent ?? false;
    setToasts((prev: Toast[]) => {
      const next = [...prev, { id, message, type, persistent }];
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
        <div role="status" aria-live="polite" style={{ position: 'fixed', top: '1.5rem', left: '50%', transform: 'translateX(-50%)', zIndex: 10001, display: 'flex', flexDirection: 'column', gap: '0.5rem', alignItems: 'center', maxWidth: 'calc(100vw - 2rem)' }}>
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
                      cursor: 'pointer', padding: '0 0 0 8px', fontSize: '18px', lineHeight: 1,
                      flexShrink: 0,
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

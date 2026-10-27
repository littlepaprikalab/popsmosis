'use client';

import { useEffect, useRef } from 'react';
import { ExclamationTriangleIcon, QuestionMarkCircleIcon, InformationCircleIcon } from '@heroicons/react/24/outline';

// Tailwind's preflight zeroes the UA `margin: auto` that centers a modal
// <dialog>, which parks it in the top-left corner. Position it explicitly:
// horizontally centered, sitting in the upper part of the viewport.
const dialogStyle: React.CSSProperties = {
  border: 'none',
  padding: 0,
  background: 'transparent',
  maxWidth: '90vw',
  width: '420px',
  position: 'fixed',
  top: 0,
  bottom: 'auto',
  left: 0,
  right: 0,
  margin: '12vh auto 0',
  maxHeight: '80vh',
  overflowY: 'auto',
};

const panelStyle: React.CSSProperties = {
  background: 'var(--bg-secondary)',
  border: '1px solid var(--border)',
  borderRadius: '12px',
  padding: '24px',
  color: 'var(--text-primary)',
  boxShadow: 'var(--dialog-shadow)',
};

const titleRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
  margin: '0 0 8px',
};

const iconStyle = (color: string): React.CSSProperties => ({
  width: '22px',
  height: '22px',
  flexShrink: 0,
  color,
});

const headingStyle: React.CSSProperties = { margin: 0, fontSize: '16px', fontWeight: 600 };

const messageStyle: React.CSSProperties = {
  margin: '0 0 24px',
  fontSize: '14px',
  color: 'var(--text-secondary)',
  lineHeight: 1.5,
};

const cancelBtnStyle: React.CSSProperties = {
  padding: '8px 20px',
  borderRadius: '8px',
  border: '1px solid var(--border)',
  background: 'var(--bg-tertiary)',
  color: 'var(--text-primary)',
  fontSize: '13px',
  fontWeight: 500,
  cursor: 'pointer',
  transition: 'opacity 0.15s',
};

const confirmBtnBase: React.CSSProperties = {
  padding: '8px 20px',
  borderRadius: '8px',
  border: 'none',
  color: 'var(--btn-filled-text)',
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
  transition: 'opacity 0.15s',
};

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'danger' | 'default';
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'default',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    else if (!open && el.open) el.close();
  }, [open]);

  const accentVar = variant === 'danger' ? 'var(--accent-red)' : 'var(--accent-brand)';
  const confirmText = variant === 'danger' ? 'var(--btn-filled-text)' : 'var(--accent-brand-text)';
  const IconComponent = variant === 'danger' ? ExclamationTriangleIcon : QuestionMarkCircleIcon;

  return (
    <dialog
      ref={dialogRef}
      onClose={onCancel}
      onClick={(e) => { if (e.target === dialogRef.current) onCancel(); }}
      style={dialogStyle}
    >
      <div style={panelStyle}>
        <div style={titleRowStyle}>
          <IconComponent style={iconStyle(accentVar)} />
          <h3 style={headingStyle}>{title}</h3>
        </div>
        <div style={messageStyle}>{message}</div>
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onCancel}
            style={cancelBtnStyle}
            onMouseEnter={e => (e.currentTarget.style.opacity = '0.8')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            autoFocus
            onClick={onConfirm}
            style={{ ...confirmBtnBase, background: accentVar, color: confirmText }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '0.85')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}

interface AlertDialogProps {
  open: boolean;
  title: string;
  message: string;
  buttonLabel?: string;
  onClose: () => void;
}

export function AlertDialog({
  open,
  title,
  message,
  buttonLabel = 'OK',
  onClose,
}: AlertDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    else if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      onClick={(e) => { if (e.target === dialogRef.current) onClose(); }}
      style={dialogStyle}
    >
      <div style={panelStyle}>
        <div style={titleRowStyle}>
          <InformationCircleIcon style={iconStyle('var(--accent-blue)')} />
          <h3 style={headingStyle}>{title}</h3>
        </div>
        <p style={messageStyle}>{message}</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button
            type="button"
            autoFocus
            onClick={onClose}
            style={{ ...confirmBtnBase, background: 'var(--accent-blue)' }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '0.85')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
          >
            {buttonLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}

'use client';

import { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error('[ErrorBoundary]', error);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="text-center py-12 px-4">
          <p className="text-sm mb-3" style={{ color: 'var(--accent-red)' }}>
            Something went wrong
          </p>
          <p className="text-xs mb-4" style={{ color: 'var(--text-muted)' }}>
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="px-4 py-2 text-xs font-bold uppercase tracking-wider rounded transition-colors"
            style={{ color: 'var(--accent-red)', border: '1px solid var(--accent-red)' }}
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * Inline error display with retry button for data fetching failures.
 */
export function FetchError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="text-center py-8">
      <p className="text-sm mb-2" style={{ color: 'var(--accent-red)' }}>
        Failed to load
      </p>
      <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-3 py-1.5 text-xs font-bold uppercase tracking-wider rounded transition-colors hover:opacity-80"
          style={{ color: 'var(--accent-red)', border: '1px solid var(--accent-red)' }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

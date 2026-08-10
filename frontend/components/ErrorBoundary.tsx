'use client';

import { Component, type ReactNode } from 'react';
import { useT } from '@/lib/i18n';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

// Class components can't use hooks. The default fallback is rendered
// via a functional sub-component so the strings honour the user's locale.
function DefaultErrorFallback({
  error,
  onRetry,
}: {
  error: Error | null;
  onRetry: () => void;
}) {
  const t = useT();
  return (
    <div
      style={{
        padding: 32,
        textAlign: 'center',
        color: '#c0392b',
        background: '#ffeaea',
        borderRadius: 12,
        margin: 24,
      }}
    >
      <p style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>
        {t('error.title')}
      </p>
      <p style={{ fontSize: 13, color: '#888' }}>
        {error?.message || t('error.unknown')}
      </p>
      <button
        onClick={onRetry}
        style={{
          marginTop: 12,
          padding: '8px 18px',
          background: '#2d6a4f',
          color: '#fff',
          border: 'none',
          borderRadius: 8,
          cursor: 'pointer',
          fontWeight: 600,
        }}
      >
        {t('common.retry')}
      </button>
    </div>
  );
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <DefaultErrorFallback
          error={this.state.error}
          onRetry={() => this.setState({ hasError: false, error: null })}
        />
      );
    }
    return this.props.children;
  }
}

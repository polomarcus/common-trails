'use client';

import { useState, useEffect, useCallback } from 'react';
import { API_URL } from '@/lib/api-client';
import { useT } from '@/lib/i18n';

interface Tag {
  id: string;
  route_id: string;
  version_id: string;
  tag: string;
  message: string | null;
}

interface Props {
  routeId: string;
  isOwner: boolean;
  currentVersionId: string | null;
}

export default function RouteTagsSection({ routeId, isOwner, currentVersionId }: Props) {
  const t = useT();
  const [tags, setTags] = useState<Tag[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [tagName, setTagName] = useState('');
  const [tagMessage, setTagMessage] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  const fetchTags = useCallback(async () => {
    try {
      const resp = await fetch(`${API_URL}/routes/${routeId}/tags`);
      if (resp.ok) setTags(await resp.json());
    } catch { /* ignore */ }
  }, [routeId]);

  useEffect(() => { fetchTags(); }, [fetchTags]);

  const createTag = async () => {
    if (!tagName.trim() || !currentVersionId) return;
    setCreating(true);
    setError('');
    try {
      const resp = await fetch(`${API_URL}/routes/${routeId}/tags`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          tag: tagName.trim(),
          version_id: currentVersionId,
          message: tagMessage.trim() || null,
        }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => null);
        setError(data?.detail || t('tag.createError'));
      } else {
        setTagName('');
        setTagMessage('');
        setShowForm(false);
        await fetchTags();
      }
    } catch {
      setError(t('tag.networkError'));
    } finally {
      setCreating(false);
    }
  };

  if (tags.length === 0 && !isOwner) return null;

  return (
    <div style={{ borderTop: '1px solid #f0f0ec', marginTop: 8 }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          width: '100%', textAlign: 'left',
          padding: '8px 0', fontSize: 12, fontWeight: 700, color: '#1a4731',
          display: 'flex', alignItems: 'center', gap: 6,
        }}
      >
        <span style={{ fontSize: 10 }}>{expanded ? '▼' : '▶'}</span>
        {t('tag.title', { count: tags.length })}
      </button>

      {expanded && (
        <div style={{ padding: '0 0 8px' }}>
          {tags.length === 0 && (
            <div style={{ fontSize: 11, color: '#999', marginBottom: 6 }}>{t('tag.none')}</div>
          )}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
            {tags.map((t) => (
              <span key={t.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 6,
                  background: '#f0f0f0', color: '#333',
                }}>
                  {t.tag}
                </span>
                <a
                  href={`${API_URL}/routes/${routeId}/tags/${t.tag}/gpx`}
                  download
                  style={{
                    fontSize: 9, fontWeight: 600, padding: '2px 6px', borderRadius: 4,
                    background: '#e8f5e9', color: '#2d6a4f', textDecoration: 'none',
                  }}
                >
                  GPX
                </a>
                {t.message && (
                  <span style={{ fontSize: 10, color: '#666', fontStyle: 'italic' }}>
                    {t.message}
                  </span>
                )}
              </span>
            ))}
          </div>

          {isOwner && currentVersionId && !showForm && (
            <button
              onClick={() => setShowForm(true)}
              style={{
                background: '#2d6a4f', color: '#fff', border: 'none', borderRadius: 8,
                fontSize: 11, fontWeight: 700, padding: '4px 12px', cursor: 'pointer',
              }}
            >
              {t('tag.add')}
            </button>
          )}

          {showForm && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
              <input
                value={tagName}
                onChange={(e) => setTagName(e.target.value)}
                placeholder={t('tag.nameLabel')}
                style={{
                  fontSize: 11, padding: '4px 8px', borderRadius: 6,
                  border: '1px solid #ccc', outline: 'none',
                }}
              />
              <input
                value={tagMessage}
                onChange={(e) => setTagMessage(e.target.value)}
                placeholder={t('tag.messageLabel')}
                style={{
                  fontSize: 11, padding: '4px 8px', borderRadius: 6,
                  border: '1px solid #ccc', outline: 'none',
                }}
              />
              {error && <div style={{ fontSize: 10, color: '#c62828' }}>{error}</div>}
              <div style={{ display: 'flex', gap: 4 }}>
                <button
                  onClick={createTag}
                  disabled={creating || !tagName.trim()}
                  style={{
                    background: '#2d6a4f', color: '#fff', border: 'none', borderRadius: 8,
                    fontSize: 11, fontWeight: 700, padding: '4px 12px', cursor: 'pointer',
                    opacity: creating || !tagName.trim() ? 0.5 : 1,
                  }}
                >
                  {creating ? '...' : t('common.create')}
                </button>
                <button
                  onClick={() => { setShowForm(false); setError(''); }}
                  style={{
                    background: '#f0f0f0', color: '#333', border: 'none', borderRadius: 8,
                    fontSize: 11, fontWeight: 700, padding: '4px 12px', cursor: 'pointer',
                  }}
                >
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

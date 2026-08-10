'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import Link from 'next/link';
import TopNav from '@/components/TopNav';
import ProfileSelector from '@/components/ProfileSelector';
import RouteCard, { type RouteCardRoute } from '@/components/RouteCard';
import { Profile, profileToSports } from '@/lib/profile';
import { SPORT_COLORS } from '@/lib/constants';
import { API_URL } from '@/lib/api-client';
import { getToken } from '@/lib/auth';
import { useT } from '@/lib/i18n';

type SortMode = 'last_accessed' | 'created_at' | 'distance_m';

interface Collection {
  id: string;
  name: string;
  description?: string;
  route_count: number;
}

export default function MeRoutesPage() {
  const t = useT();
  const [profile, setProfile] = useState<Profile>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortMode>('last_accessed');
  const [routes, setRoutes] = useState<RouteCardRoute[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showTrash, setShowTrash] = useState(false);
  const [newCollName, setNewCollName] = useState('');
  const [creatingColl, setCreatingColl] = useState(false);

  const token = typeof window !== 'undefined' ? getToken() : null;

  const loadRoutes = useCallback(async () => {
    if (!token) { setError(t('meRoutes.loginRequired')); setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ sort, limit: '200', include_deleted: 'true' });
      const sports = profileToSports(profile);
      if (sports.length === 1) params.set('sport', sports[0]);
      if (search.trim()) params.set('search', search.trim());
      const resp = await fetch(`${API_URL}/me/routes?${params}`, {
        credentials: 'include',
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setRoutes(await resp.json());
    } catch (err: unknown) {
      setError(t('meRoutes.loadError', { error: err instanceof Error ? err.message : String(err) }));
    } finally { setLoading(false); }
  }, [token, profile, sort, search]);

  const loadCollections = useCallback(async () => {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL}/me/collections`, {
        credentials: 'include',
      });
      if (resp.ok) setCollections(await resp.json());
    } catch { /* ignore */ }
  }, [token]);

  useEffect(() => { loadRoutes(); }, [loadRoutes]);
  useEffect(() => { loadCollections(); }, [loadCollections]);

  // Debounce search
  const [searchInput, setSearchInput] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const activeRoutes = useMemo(() => routes.filter(r => !r.deleted_at), [routes]);
  const deletedRoutes = useMemo(() => {
    const now = Date.now();
    const thirtyDays = 30 * 24 * 60 * 60 * 1000;
    return routes.filter(r => r.deleted_at && (now - new Date(r.deleted_at).getTime()) < thirtyDays);
  }, [routes]);

  // Filter by sport on the client (for multi-sport profiles like offroad)
  const filteredRoutes = useMemo(() => {
    const sports = profileToSports(profile);
    if (profile === 'all') return activeRoutes;
    return activeRoutes.filter(r => sports.includes(r.sport));
  }, [activeRoutes, profile]);

  const drafts = useMemo(() => filteredRoutes.filter(r => r.status === 'draft'), [filteredRoutes]);
  const published = useMemo(() => filteredRoutes.filter(r => r.status !== 'draft'), [filteredRoutes]);

  const handleDelete = async (id: string) => {
    if (!token) return;
    if (!confirm(t('meRoutes.deleteConfirm'))) return;
    await fetch(`${API_URL}/routes/${id}`, { method: 'DELETE', credentials: 'include' });
    loadRoutes();
  };

  const handleRestore = async (id: string) => {
    if (!token) return;
    await fetch(`${API_URL}/routes/${id}/restore`, { method: 'POST', credentials: 'include' });
    loadRoutes();
  };

  const handleToggleVisibility = async (id: string, visibility: string) => {
    if (!token) return;
    await fetch(`${API_URL}/routes/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ visibility }),
    });
    loadRoutes();
  };

  const handleCreateCollection = async () => {
    if (!token || !newCollName.trim()) return;
    setCreatingColl(true);
    try {
      await fetch(`${API_URL}/me/collections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ name: newCollName.trim() }),
      });
      setNewCollName('');
      loadCollections();
    } finally { setCreatingColl(false); }
  };

  const handleDeleteCollection = async (id: string) => {
    if (!token) return;
    if (!confirm(t('meRoutes.deleteGroupConfirm'))) return;
    await fetch(`${API_URL}/me/collections/${id}`, { method: 'DELETE', credentials: 'include' });
    loadCollections();
  };

  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav activeHref="/me/routes" />

      <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#1a1a1a', marginBottom: 16 }}>
          {t('meRoutes.title')}
        </h1>

        {/* Controls */}
        <div style={{
          background: '#fff', borderRadius: 12, padding: '12px 16px', marginBottom: 16,
          display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center',
        }}>
          <ProfileSelector value={profile} onChange={setProfile} />
          <div style={{ width: 1, height: 28, background: '#eee' }} />
          <input
            type="text"
            placeholder={t('meRoutes.search')}
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            style={{
              padding: '6px 12px', border: '1.5px solid #ddd', borderRadius: 6,
              fontSize: 13, width: 180, outline: 'none',
            }}
          />
          <select
            value={sort}
            onChange={e => setSort(e.target.value as SortMode)}
            style={{
              padding: '6px 12px', border: '1.5px solid #ddd', borderRadius: 6,
              fontSize: 13, background: '#fff', cursor: 'pointer',
            }}
          >
            <option value="last_accessed">{t('meRoutes.sortLastAccessed')}</option>
            <option value="created_at">{t('meRoutes.sortCreatedAt')}</option>
            <option value="distance_m">{t('meRoutes.sortDistance')}</option>
          </select>
        </div>

        {error && (
          <div style={{
            padding: '12px 16px', background: '#ffeaea', borderRadius: 8,
            color: '#c0392b', marginBottom: 16, fontSize: 13,
          }}>
            {error}
          </div>
        )}

        {loading && (
          <div style={{ textAlign: 'center', padding: 48, color: '#888' }}>
            {t('common.loading')}
          </div>
        )}

        {!loading && !error && filteredRoutes.length === 0 && (
          <div style={{
            background: '#fff', borderRadius: 12, padding: '48px 24px',
            textAlign: 'center', color: '#888',
          }}>
            <p style={{ fontSize: 32, marginBottom: 12 }}>{'\u{1F5FA}\u{FE0F}'}</p>
            <p style={{ fontSize: 15, marginBottom: 8 }}>{t('meRoutes.noRoutes')}</p>
            <Link href="/map" style={{ color: '#2d6a4f', fontWeight: 600, fontSize: 14 }}>
              {t('meRoutes.createOnMap')}
            </Link>
          </div>
        )}

        {!loading && !error && (
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            {/* Route list (left column) */}
            <div style={{ flex: '1 1 55%', minWidth: 320 }}>
              {/* Drafts */}
              {drafts.length > 0 && (
                <div style={{ marginBottom: 20 }}>
                  <h2 style={{ fontSize: 14, fontWeight: 700, color: '#e67e22', marginBottom: 8 }}>
                    {t('meRoutes.drafts', { count: drafts.length })}
                  </h2>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {drafts.map(r => (
                      <RouteCard key={r.id} route={r} showActions="owner"
                        onDelete={handleDelete} onToggleVisibility={handleToggleVisibility} />
                    ))}
                  </div>
                </div>
              )}

              {/* Published */}
              {published.length > 0 && (
                <div style={{ marginBottom: 20 }}>
                  <h2 style={{ fontSize: 14, fontWeight: 700, color: '#555', marginBottom: 8 }}>
                    {t('meRoutes.routes', { count: published.length })}
                  </h2>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {published.map(r => (
                      <RouteCard key={r.id} route={r} showActions="owner"
                        onDelete={handleDelete} onToggleVisibility={handleToggleVisibility} />
                    ))}
                  </div>
                </div>
              )}

              {/* Trash */}
              {deletedRoutes.length > 0 && (
                <div style={{ marginBottom: 20 }}>
                  <button
                    onClick={() => setShowTrash(!showTrash)}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      fontSize: 14, fontWeight: 700, color: '#999', marginBottom: 8,
                    }}
                  >
                    {showTrash ? '\u25BC' : '\u25B6'} {t('meRoutes.trash', { count: deletedRoutes.length })}
                  </button>
                  {showTrash && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      {deletedRoutes.map(r => (
                        <RouteCard key={r.id} route={r} showActions="owner" onRestore={handleRestore} />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Collections */}
              <div style={{ marginBottom: 20 }}>
                <h2 style={{ fontSize: 14, fontWeight: 700, color: '#555', marginBottom: 8 }}>
                  {t('meRoutes.groups')}
                </h2>
                {collections.map(c => (
                  <div key={c.id} style={{
                    background: '#fff', borderRadius: 10, padding: '12px 16px',
                    marginBottom: 8, boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
                    display: 'flex', alignItems: 'center', gap: 12,
                  }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 700, fontSize: 14 }}>{c.name}</div>
                      <div style={{ fontSize: 12, color: '#888' }}>{t('meRoutes.routeCount', { count: c.route_count, s: c.route_count !== 1 ? 's' : '' })}</div>
                    </div>
                    <Link
                      href={`/map?compare_collection=${c.id}`}
                      style={{
                        padding: '5px 10px', background: '#e8f5e9', color: '#2d6a4f',
                        borderRadius: 6, fontSize: 12, fontWeight: 600, textDecoration: 'none',
                      }}
                    >
                      {t('meRoutes.compareOnMap')}
                    </Link>
                    <button
                      onClick={() => handleDeleteCollection(c.id)}
                      style={{
                        padding: '5px 10px', background: '#fff', color: '#c62828',
                        border: '1px solid #ef5350', borderRadius: 6, fontSize: 12,
                        cursor: 'pointer',
                      }}
                    >
                      {t('meRoutes.deleteGroup')}
                    </button>
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <input
                    type="text"
                    placeholder={t('meRoutes.newGroup')}
                    value={newCollName}
                    onChange={e => setNewCollName(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleCreateCollection()}
                    style={{
                      flex: 1, padding: '6px 12px', border: '1.5px solid #ddd',
                      borderRadius: 6, fontSize: 13, outline: 'none',
                    }}
                  />
                  <button
                    onClick={handleCreateCollection}
                    disabled={creatingColl || !newCollName.trim()}
                    style={{
                      padding: '6px 14px', background: '#2d6a4f', color: '#fff',
                      border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 700,
                      cursor: creatingColl ? 'not-allowed' : 'pointer',
                      opacity: creatingColl || !newCollName.trim() ? 0.5 : 1,
                    }}
                  >
                    {t('meRoutes.createGroup')}
                  </button>
                </div>
              </div>
            </div>

            {/* Mini-map (right column) */}
            <div style={{ flex: '1 1 35%', minWidth: 280, maxWidth: 400 }}>
              <div style={{
                background: '#fff', borderRadius: 12, padding: 16,
                boxShadow: '0 1px 4px rgba(0,0,0,0.05)', position: 'sticky', top: 68,
              }}>
                <h3 style={{ fontSize: 13, fontWeight: 700, color: '#555', marginBottom: 12 }}>
                  {t('meRoutes.yourRoutes')}
                </h3>
                <div style={{
                  width: '100%', height: 300, background: '#e8e8e0', borderRadius: 8,
                  position: 'relative', overflow: 'hidden',
                }}>
                  {/* Simple dots representing route centers */}
                  <svg viewBox="-180 -90 360 180" style={{ width: '100%', height: '100%' }}>
                    {filteredRoutes.filter(r => r.center).map(r => (
                      <circle
                        key={r.id}
                        cx={r.center![0]}
                        cy={-r.center![1]}
                        r={1.5}
                        fill={SPORT_COLORS[r.sport] ?? '#2d6a4f'}
                        opacity={0.8}
                      />
                    ))}
                  </svg>
                </div>
                <div style={{ marginTop: 8, fontSize: 12, color: '#aaa', textAlign: 'center' }}>
                  {t('meRoutes.routeCount', { count: filteredRoutes.length, s: filteredRoutes.length !== 1 ? 's' : '' })}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

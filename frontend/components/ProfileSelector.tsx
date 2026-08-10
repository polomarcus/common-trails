'use client';

import { useEffect, useState, memo } from 'react';
import {
  Profile,
  PROFILE_LABELS,
  PROFILE_ICONS,
  getStoredProfile,
  setStoredProfile,
} from '@/lib/profile';

interface ProfileSelectorProps {
  value?: Profile;
  onChange: (profile: Profile) => void;
  /** If true, persist selection to localStorage automatically */
  persist?: boolean;
}

const PROFILES: Profile[] = ['all', 'road', 'gravel', 'mtb', 'offroad', 'running'];

/**
 * Segmented control for selecting a cycling profile.
 * Persists to localStorage if persist=true.
 */
export default memo(function ProfileSelector({ value, onChange, persist = true }: ProfileSelectorProps) {
  const [selected, setSelected] = useState<Profile>(value ?? 'all');

  // Initialize from localStorage on mount
  useEffect(() => {
    if (value === undefined && persist) {
      const stored = getStoredProfile();
      setSelected(stored);
      onChange(stored);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync with controlled value
  useEffect(() => {
    if (value !== undefined) {
      setSelected(value);
    }
  }, [value]);

  const handleSelect = (profile: Profile) => {
    setSelected(profile);
    if (persist) {
      setStoredProfile(profile);
    }
    onChange(profile);
  };

  return (
    <div
      style={{
        display: 'inline-flex',
        background: '#f0f0ea',
        borderRadius: 10,
        padding: 3,
        gap: 2,
      }}
      role="group"
      aria-label="Profil vélo"
    >
      {PROFILES.map((profile) => {
        const isActive = selected === profile;
        return (
          <button
            key={profile}
            onClick={() => handleSelect(profile)}
            data-testid={`profile-${profile}`}
            aria-pressed={isActive}
            title={PROFILE_LABELS[profile]}
            style={{
              padding: '7px 14px',
              background: isActive ? '#2d6a4f' : 'transparent',
              color: isActive ? '#fff' : '#555',
              border: 'none',
              borderRadius: 8,
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: isActive ? 700 : 400,
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              transition: 'all 0.15s',
            }}
          >
            <span>{PROFILE_ICONS[profile]}</span>
            <span>{PROFILE_LABELS[profile]}</span>
          </button>
        );
      })}
    </div>
  );
});

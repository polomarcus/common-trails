"""Centralized configuration constants."""
import os

TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"
# Allow external routing calls (dormant BRouter/OSRM fallbacks).
# Disabled by default — community graph + straight-line is the production cascade.
# Tests set this to "false" via conftest.py.
ROUTING_EXTERNAL = os.environ.get("ROUTING_EXTERNAL", "false").lower() == "true"
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3787")
VALID_SPORTS = frozenset({"road", "gravel", "mtb", "offroad", "running"})

# ── Transactional email (Resend) + passwordless magic-link login ─────────────
# Resend HTTP API key. Empty/unset means "no email backend" — fine for local
# dev + TEST_MODE (email.send_email() no-ops); in prod it is wired from Secret
# Manager (see scripts/deploy-prod.sh --set-secrets).
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Sending identity. Until the chemins-communs.fr domain is DKIM/SPF/DMARC
# verified in Resend, keep the default test sender (onboarding@resend.dev),
# which only delivers to the Resend account owner. Flip to
# "Chemins Communs <bonjour@chemins-communs.fr>" once the domain is verified
# (see docs/email-auth-dns.md).
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Chemins Communs <onboarding@resend.dev>")
# Human support address shown in user-facing failure emails (matches the
# frontend /support page).
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "paul@epauler.fr")
# Magic-link token time-to-live (minutes). Short by design — a login link is
# single-use and should not linger valid in an inbox.
MAGIC_LINK_TTL_MIN = int(os.environ.get("MAGIC_LINK_TTL_MIN", "15"))

# Fernet key (base64-urlsafe 32 bytes) used to encrypt
# IntegrationAccount.access_token / refresh_token at rest. Empty/unset
# means "no encryption" — fine for local dev + TEST_MODE; in prod it
# must be wired from Secret Manager (see infra/terraform/main.tf).
STRAVA_TOKEN_ENC_KEY = os.environ.get("STRAVA_TOKEN_ENC_KEY", "")


def expand_sport(sport: str) -> list[str]:
    """Expand 'offroad' into its component sports for aggregation queries.

    After migration 0035 added a dedicated ``heat_edges_offroad`` partition,
    activities tagged ``sport=offroad`` write to that partition. So an
    aggregation query for ``offroad`` must read mtb + gravel + offroad
    (not just mtb + gravel — that was the pre-0035 mapping). Without
    'offroad' in this list, offroad users contribute heatmap data they
    never see when their own profile queries the heatmap.
    """
    return ["mtb", "offroad", "gravel"] if sport == "offroad" else [sport]

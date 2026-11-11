"""Transactional email via the Resend HTTP API.

One public function, :func:`send_email`. Callers never talk to Resend
directly — they hand over ``to`` / ``subject`` / ``html`` and this module
decides whether to actually send.

Behaviour matrix:

* ``TEST_MODE=true``          → NO network call. Logs + returns True so the
  test suite and local dev never send a real email (and never need a key).
* ``RESEND_API_KEY`` unset    → NO network call. Logs a warning + returns
  False (misconfiguration in a non-test env — surface it, don't crash).
* otherwise                   → POST https://api.resend.com/emails.

Resend errors NEVER crash the caller: any exception / non-2xx is logged
and turned into a ``False`` return. The magic-link request endpoint relies
on this — it must always return its generic 200 regardless of the email
backend's health (no account enumeration, no 500 on a transient SMTP hiccup).
"""
from __future__ import annotations

import html as html_lib
import logging
import math
import os

import httpx

from app.config import EMAIL_FROM, RESEND_API_KEY, SUPPORT_EMAIL, TEST_MODE

# Where "a new rider confirmed their account" notifications go. Unset → none.
ADMIN_NOTIFY_EMAIL = os.environ.get("ADMIN_NOTIFY_EMAIL", "").strip()

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_S = 10.0


def _heatmap_public_base_url() -> str:
    """Public base URL under which the community heatmap is served.

    SSOT for the "calque" (gpx.studio / VisuGPX overlay) tile URL promoted in
    transactional email. Mirrors ``build_pmtiles._public_base_url`` but the
    email service has no bucket_name in scope, so it defaults to the live prod
    bucket object URL. In prod ``HEATMAP_PUBLIC_BASE_URL`` points at the
    first-party CDN domain (``https://tiles.chemins-communs.fr``). No trailing
    slash — callers append ``/<path>``.
    """
    return os.environ.get(
        "HEATMAP_PUBLIC_BASE_URL",
        "https://storage.googleapis.com/common-trails-heatmap-prod",
    ).rstrip("/")


def send_email(to: str, subject: str, html: str) -> bool:
    """Send one transactional email. Returns True on success, False otherwise.

    Never raises — a failed send is logged and returns False so callers on
    a no-enumeration path (magic-link request) can proceed unconditionally.
    """
    if TEST_MODE:
        # No external calls in tests / local dev. Log at debug so a test run
        # stays quiet but the intent is inspectable.
        logger.debug("TEST_MODE: skipping real email send to %s (subject=%r)", to, subject)
        return True

    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY unset — cannot send email to %s (subject=%r). "
            "Wire the secret in prod (scripts/deploy-prod.sh).",
            to,
            subject,
        )
        return False

    try:
        resp = httpx.post(
            _RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — never let a send crash the request
        logger.error("Resend request failed for %s: %s", to, exc)
        return False

    if resp.status_code >= 300:
        # Log the body — Resend returns a helpful JSON error (bad key, unverified
        # domain, invalid recipient). Truncate to keep logs sane.
        logger.error(
            "Resend returned %s for %s: %s",
            resp.status_code,
            to,
            resp.text[:500],
        )
        return False

    logger.info("Sent email to %s (subject=%r)", to, subject)
    return True


def render_magic_link_email(verify_url: str, locale: str = "fr") -> tuple[str, str]:
    """Return ``(subject, html)`` for the passwordless login email.

    Bilingual (fr default, en). Plain, self-contained HTML — no external
    assets (many mail clients block them) and one obvious button.
    """
    if locale == "en":
        subject = "Your Chemins Communs login link"
        intro = "Click the button below to sign in. This link is valid for 15 minutes and can be used once."
        button = "Sign in"
        ignore = "If you did not request this, you can safely ignore this email."
    else:
        subject = "Ton lien de connexion Chemins Communs"
        intro = "Clique sur le bouton ci-dessous pour te connecter. Ce lien est valable 15 minutes et à usage unique."
        button = "Se connecter"
        ignore = "Si tu n'es pas à l'origine de cette demande, tu peux ignorer cet email."

    html = f"""\
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a1a1a">
  <h1 style="font-size:20px;color:#1a4731;margin:0 0 16px">Chemins Communs</h1>
  <p style="font-size:15px;line-height:1.5;margin:0 0 24px">{intro}</p>
  <p style="margin:0 0 24px">
    <a href="{verify_url}"
       style="display:inline-block;padding:12px 28px;background:#2d6a4f;color:#fff;
              text-decoration:none;border-radius:10px;font-weight:600;font-size:15px">
      {button}
    </a>
  </p>
  <p style="font-size:13px;color:#666;line-height:1.5;margin:0 0 8px">{ignore}</p>
  <p style="font-size:12px;color:#999;word-break:break-all;margin:0">{verify_url}</p>
</div>"""
    return subject, html


def _wrap_html(body: str) -> str:
    """Shared self-contained shell (no external assets — mail clients block them)."""
    return (
        '<div style="font-family:system-ui,-apple-system,sans-serif;max-width:480px;'
        'margin:0 auto;padding:24px;color:#1a1a1a">\n'
        '  <h1 style="font-size:20px;color:#1a4731;margin:0 0 16px">Chemins Communs</h1>\n'
        f"{body}"
        "</div>"
    )


def render_archive_done_email(
    *,
    imported: int,
    skipped: int,
    failed: int,
    locale: str = "fr",
    contribute_heatmap: bool = True,
    skipped_no_sport: int = 0,
    imported_undated: int = 0,
) -> tuple[str, str]:
    """Return ``(subject, html)`` for the "your archive finished draining" email.

    Two wordings: the happy path (>=1 trace imported) and the nothing-new path
    (``imported == 0`` — typically every member was a duplicate of
    already-imported activities or out-of-scope).

    The happy path must stay TRUTHFUL about timing (Thierry Crouzet, 2026-11):
    the user's own rides show up at once in the "Ma heatmap" layer (served live
    from the DB), but the community heatmap is a static PMTiles rebuilt by a
    job (~30 min build + cold start + pointer cache, more for an archive whose
    rebuild ping was deferred) — so never claim the traces are already on the
    heatmap; promise "usually within the hour". When the archive does NOT
    contribute to the heatmap (``contribute_heatmap=False``) no rebuild is
    triggered, so the copy only points to "Ma heatmap". The count is NOT
    repeated in the intro — the list below already carries it.

    Provider-neutral wording ("Ton archive", never "archive Strava"): the
    drain does not record which platform produced the .zip (pending_archives
    has no provider column; Garmin/Komoot/… exports go through the same path).
    ``skipped`` is the total; ``skipped_no_sport`` (members whose sport could
    not be identified — no activities.csv row, no usable FIT/GPX sport, no
    sport picked) is part of it but gets its OWN actionable line instead of
    being passed off as duplicates / out-of-scope. Strava-export planned
    routes (``routes/*.gpx``) are never yielded by the drain (no bucket); an
    ``activities/`` file the activities.csv doesn't list counts as an
    out-of-scope skip — never in "pick a sport and re-import them".

    ``imported_undated`` (part of ``imported``): traces with no date at all —
    in practice planned itineraries. They are in "Ma heatmap" but never on
    the public map (``provenance.community_eligible_conditions``), and the
    email says so on its own line rather than promising them to the
    community heatmap.
    """
    map_url = "https://chemins-communs.fr/map"
    no_sport = max(0, min(skipped_no_sport, skipped))
    other_skipped = skipped - no_sport
    undated = max(0, min(imported_undated, imported))
    # Only promise the community heatmap when at least one DATED trace feeds it.
    promise_public = contribute_heatmap and imported > undated
    # The community heatmap is also published as a raster XYZ tile pyramid on
    # public GCS, so it can be added as a custom OVERLAY ("calque") in gpx.studio
    # / VisuGPX via one URL. Promote it in the done email — closes the loop
    # ("your archive is in → here's how to plan on top of the whole heatmap").
    calque_url = f"{_heatmap_public_base_url()}/raster/tiles.json"
    if locale == "en":
        if imported > 0:
            subject = "Your archive has been imported 🚴"
            intro = (
                "Your archive has been processed. Your rides are visible right away in the "
                "<strong>My heatmap</strong> layer on the map"
                + ("; the community heatmap will include them, usually within the hour." if promise_public else ".")
            )
        else:
            subject = "Your archive has been processed"
            intro = (
                "Your archive has been processed, but no new trace was added — "
                "they were most likely already imported (duplicates) or out of scope "
                "(virtual rides, other sports…)."
            )
        lines = [f"<strong>{imported}</strong> imported"]
        if other_skipped:
            lines.append(f"<strong>{other_skipped}</strong> skipped (out of scope / duplicates)")
        if undated:
            lines.append(
                f"<strong>{undated}</strong> with no date (planned routes?) — "
                "visible in My heatmap only, not on the public map"
            )
        if no_sport:
            lines.append(
                f"<strong>{no_sport}</strong> with no identifiable sport — "
                "pick a sport and re-import them"
            )
        if failed:
            lines.append(f"<strong>{failed}</strong> failed")
        button = "See the map"
        calque_title = "💡 Did you know?"
        calque_body = (
            "You can display the whole community heatmap as an overlay in "
            "<strong>gpx.studio</strong> or <strong>VisuGPX</strong> and plan your route on top of it — "
            "add a custom map layer with this tile URL:"
        )
    else:
        if imported > 0:
            subject = "Ton archive est importée 🚴"
            intro = (
                "Ton archive a été traitée. Tes sorties sont visibles dès maintenant "
                "dans le calque <strong>Ma heatmap</strong> de la carte"
                + (" ; la heatmap communautaire les intégrera, en général dans l'heure." if promise_public else ".")
            )
        else:
            subject = "Ton archive a été traitée"
            intro = (
                "Ton archive a été traitée, mais aucune nouvelle trace n'a été "
                "ajoutée — elles étaient probablement déjà importées (doublons) ou hors "
                "périmètre (sorties virtuelles, autres sports…)."
            )
        lines = [f"<strong>{imported}</strong> importée{'s' if imported > 1 else ''}"]
        if other_skipped:
            lines.append(
                f"<strong>{other_skipped}</strong> ignorée{'s' if other_skipped > 1 else ''} "
                "(hors-scope / doublons)"
            )
        if undated:
            lines.append(
                f"<strong>{undated}</strong> trace{'s' if undated > 1 else ''} sans date "
                "(itinéraires planifiés ?) : visible"
                f"{'s' if undated > 1 else ''} dans Ma heatmap uniquement, "
                "pas sur la carte publique"
            )
        if no_sport:
            lines.append(
                f"<strong>{no_sport}</strong> sans sport identifiable — "
                "choisis un sport et réimporte-les"
            )
        if failed:
            lines.append(f"<strong>{failed}</strong> en erreur")
        button = "Voir la carte"
        calque_title = "💡 Le savais-tu ?"
        calque_body = (
            "Tu peux afficher toute la heatmap communautaire en calque dans "
            "<strong>gpx.studio</strong> ou <strong>VisuGPX</strong> et tracer ton itinéraire par-dessus — "
            "ajoute une couche de carte personnalisée avec cette URL de tuiles :"
        )

    items = "".join(f'<li style="margin:0 0 4px">{li}</li>' for li in lines)
    body = f"""\
  <p style="font-size:15px;line-height:1.5;margin:0 0 16px">{intro}</p>
  <ul style="font-size:15px;line-height:1.5;margin:0 0 24px;padding-left:20px">{items}</ul>
  <p style="margin:0 0 24px">
    <a href="{map_url}"
       style="display:inline-block;padding:12px 28px;background:#2d6a4f;color:#fff;
              text-decoration:none;border-radius:10px;font-weight:600;font-size:15px">
      {button}
    </a>
  </p>
  <p style="font-size:12px;color:#999;word-break:break-all;margin:0 0 28px">{map_url}</p>
  <div style="border:1px solid #d8e6df;background:#f4faf7;border-radius:10px;padding:14px 16px">
    <p style="font-size:14px;font-weight:600;margin:0 0 6px">{calque_title}</p>
    <p style="font-size:14px;line-height:1.5;color:#333;margin:0 0 8px">{calque_body}</p>
    <p style="font-size:12px;color:#2d6a4f;word-break:break-all;font-family:monospace;margin:0">{calque_url}</p>
  </div>
"""
    return subject, _wrap_html(body)


def format_duration_estimate(seconds: float, locale: str = "fr") -> str:
    """Friendly, rounded-UP duration for the start-of-import email.

    Never "0 minute": anything under a minute reads "about 1 minute". Under
    10 min → to the minute; under an hour → next 5 min; beyond → next half
    hour ("environ 1 heure", "environ 1 h 30", "environ 2 heures").
    """
    minutes = max(1, math.ceil(max(0.0, float(seconds)) / 60))
    if minutes >= 10:
        minutes = math.ceil(minutes / 5) * 5
    if minutes >= 60:
        minutes = math.ceil(minutes / 30) * 30
        hours, rest = divmod(minutes, 60)
        if locale == "en":
            label = f"{hours} hour{'s' if hours > 1 else ''}"
            return f"about {label}" + (" 30 minutes" if rest else "")
        if rest:
            return f"environ {hours} h 30"
        return f"environ {hours} heure{'s' if hours > 1 else ''}"
    if locale == "en":
        return f"about {minutes} minute{'s' if minutes > 1 else ''}"
    return f"environ {minutes} minute{'s' if minutes > 1 else ''}"


def render_archive_started_email(
    *,
    members: int,
    estimated_seconds: float,
    locale: str = "fr",
) -> tuple[str, str]:
    """Return ``(subject, html)`` for the "your import has started" email.

    Sent ONCE when the drain actually starts processing the archive (not at
    upload, so queue time doesn't skew the estimate). Same shell / styling /
    provider-neutral wording as :func:`render_archive_done_email`, which
    follows at the end. The duration is an ESTIMATE ("environ …"); the copy
    says so and promises the end-of-import email.
    """
    map_url = "https://chemins-communs.fr/map"
    duration = format_duration_estimate(estimated_seconds, locale)
    if locale == "en":
        subject = f"Your import has started ({duration})"
        intro = (
            "Processing of your archive has just started: "
            f"<strong>{members}</strong> activity file{'s' if members != 1 else ''} to import, "
            f"estimated at <strong>{duration}</strong>."
        )
        detail = (
            "Your rides appear progressively in the <strong>My heatmap</strong> layer "
            "on the map. You will receive a second email as soon as the import is finished."
        )
        button = "See the map"
    else:
        subject = f"Début de ton import, estimé à {duration}"
        intro = (
            "Le traitement de ton archive vient de commencer : "
            f"<strong>{members}</strong> fichier{'s' if members > 1 else ''} d'activité à importer, "
            f"durée estimée à <strong>{duration}</strong>."
        )
        detail = (
            "Tes sorties apparaissent au fur et à mesure dans le calque "
            "<strong>Ma heatmap</strong> de la carte. Tu recevras un second email "
            "dès que l'import sera terminé."
        )
        button = "Voir la carte"
    body = f"""\
  <p style="font-size:15px;line-height:1.5;margin:0 0 16px">{intro}</p>
  <p style="font-size:15px;line-height:1.5;margin:0 0 24px">{detail}</p>
  <p style="margin:0 0 24px">
    <a href="{map_url}"
       style="display:inline-block;padding:12px 28px;background:#2d6a4f;color:#fff;
              text-decoration:none;border-radius:10px;font-weight:600;font-size:15px">
      {button}
    </a>
  </p>
  <p style="font-size:12px;color:#999;word-break:break-all;margin:0">{map_url}</p>
"""
    return subject, _wrap_html(body)


_ARCHIVE_TOO_BIG_MARKERS = ("too many members", "too large", "zip-bomb", "decompresses to")


def render_archive_failed_email(last_error: str | None, locale: str = "fr") -> tuple[str, str]:
    """Return ``(subject, html)`` for the "your archive could not be processed" email.

    The stored ``last_error`` is mapped onto a WHITELIST of user-friendly
    reasons — the raw text (which may embed exception details) is NEVER put in
    the email. Unknown errors get the generic "retry or contact us" copy.
    """
    low = (last_error or "").lower()
    too_big = any(m in low for m in _ARCHIVE_TOO_BIG_MARKERS)
    if locale == "en":
        subject = "Your archive could not be processed"
        if too_big:
            reason = (
                "Your export is too large for a single upload — please split it "
                "into several smaller .zip files and upload them one by one."
            )
        else:
            reason = (
                "Something went wrong while processing your archive. Please try "
                f"again, or contact us at {SUPPORT_EMAIL} and we will look into it."
            )
    else:
        subject = "Ton archive n'a pas pu être traitée"
        if too_big:
            reason = (
                "Ton export est trop volumineux pour un seul envoi — découpe-le "
                "en plusieurs .zip plus petits et envoie-les un par un."
            )
        else:
            reason = (
                "Une erreur est survenue pendant le traitement de ton archive. "
                f"Réessaie, ou écris-nous à {SUPPORT_EMAIL} et nous regarderons."
            )

    body = f"""\
  <p style="font-size:15px;line-height:1.5;margin:0 0 24px">{reason}</p>
  <p style="font-size:13px;color:#666;line-height:1.5;margin:0">
    <a href="mailto:{SUPPORT_EMAIL}" style="color:#2d6a4f">{SUPPORT_EMAIL}</a>
  </p>
"""
    return subject, _wrap_html(body)


def render_resync_reminder_email() -> tuple[str, str]:
    """Return ``(subject, html)`` for the yearly "re-sync your data" reminder.

    Bilingual in ONE email — French first, English below (Paul's brief) — because
    the reminder is sent to the whole contributor base regardless of UI locale.
    The community heatmap is fed by MANUAL uploads only (Strava-API stays
    personal), so a contributor's rides since their last export never reach the
    map; this nudges them to re-export from Strava/Garmin and re-upload. Same
    self-contained, asset-free shell as the other transactional emails.
    """
    subject = "Tes dernières sorties manquent sur la carte 🚴 / Your latest rides are missing from the map"
    map_url = "https://chemins-communs.fr/strava"
    strava_url = "https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export"
    garmin_url = "https://www.garmin.com/fr-FR/account/datamanagement/exportdata"

    body = f"""\
  <p style="font-size:15px;line-height:1.5;margin:0 0 16px">
    Bonjour,
  </p>
  <p style="font-size:15px;line-height:1.5;margin:0 0 16px">
    La carte communautaire n'est enrichie que par les fichiers que tu
    <strong>importes toi-même</strong> — tes sorties récentes n'y sont donc pas
    encore. Pense à ré-exporter tes données depuis
    <a href="{strava_url}" style="color:#2d6a4f">Strava</a> ou
    <a href="{garmin_url}" style="color:#2d6a4f">Garmin</a> et à les redéposer :
    chaque trace partagée renforce les chemins communs (sous licence ODbL, tes
    départs et arrivées restent masqués).
  </p>
  <p style="margin:0 0 24px">
    <a href="{map_url}"
       style="display:inline-block;padding:12px 28px;background:#2d6a4f;color:#fff;
              text-decoration:none;border-radius:10px;font-weight:600;font-size:15px">
      Mettre à jour mes traces
    </a>
  </p>
  <hr style="border:none;border-top:1px solid #e0e0e0;margin:0 0 24px" />
  <p style="font-size:14px;line-height:1.5;color:#444;margin:0 0 12px">
    <em>Hi,</em>
  </p>
  <p style="font-size:14px;line-height:1.5;color:#444;margin:0 0 12px">
    <em>The community map only grows from the files you
    <strong>upload yourself</strong> — so your recent rides aren't on it yet.
    Re-export your data from
    <a href="{strava_url}" style="color:#2d6a4f">Strava</a> or
    <a href="{garmin_url}" style="color:#2d6a4f">Garmin</a> and upload it again:
    every shared trace strengthens the common trails (ODbL-licensed; your start
    and end points stay masked).</em>
  </p>
  <p style="font-size:14px;line-height:1.5;color:#444;margin:0 0 24px">
    <em><a href="{map_url}" style="color:#2d6a4f">Update my traces →</a></em>
  </p>
"""
    return subject, _wrap_html(body)


def render_email_change_email(confirm_url: str, locale: str = "fr") -> tuple[str, str]:
    """Return ``(subject, html)`` for the confirm-your-new-email link.

    Sent to the NEW address when a user sets/changes the email on their account
    (account unification — e.g. a Strava-OAuth user replacing their synthetic
    ``strava_<id>@strava.local`` address). Clicking finalizes the change; until
    then the account keeps its previous address, so we never bind an unverified
    email. Same self-contained, asset-free style as the login email.
    """
    if locale == "en":
        subject = "Confirm your Chemins Communs email"
        intro = (
            "Confirm this address to attach it to your Chemins Communs account. "
            "This link is valid for 15 minutes and can be used once."
        )
        button = "Confirm my email"
        ignore = "If you did not request this, you can safely ignore this email."
    else:
        subject = "Confirme ton email Chemins Communs"
        intro = (
            "Confirme cette adresse pour la rattacher à ton compte Chemins "
            "Communs. Ce lien est valable 15 minutes et à usage unique."
        )
        button = "Confirmer mon email"
        ignore = "Si tu n'es pas à l'origine de cette demande, tu peux ignorer cet email."

    html = f"""\
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a1a1a">
  <h1 style="font-size:20px;color:#1a4731;margin:0 0 16px">Chemins Communs</h1>
  <p style="font-size:15px;line-height:1.5;margin:0 0 24px">{intro}</p>
  <p style="margin:0 0 24px">
    <a href="{confirm_url}"
       style="display:inline-block;padding:12px 28px;background:#2d6a4f;color:#fff;
              text-decoration:none;border-radius:10px;font-weight:600;font-size:15px">
      {button}
    </a>
  </p>
  <p style="font-size:13px;color:#666;line-height:1.5;margin:0 0 8px">{ignore}</p>
  <p style="font-size:12px;color:#999;word-break:break-all;margin:0">{confirm_url}</p>
</div>"""
    return subject, html


def render_admin_new_account_email(new_email: str) -> tuple[str, str]:
    """``(subject, html)`` of the admin notification sent when a new account
    confirms its address (first successful magic-link login). Internal mail:
    French only, no marketing — the address, and a link to the admin page."""
    safe = html_lib.escape(new_email)
    subject = f"Nouveau compte sur Chemins Communs : {new_email}"
    body = f"""\
  <p style="font-size:15px;line-height:1.5;margin:0 0 12px">
    Un nouveau compte vient de confirmer son adresse :
  </p>
  <p style="font-size:16px;font-weight:700;margin:0 0 20px">{safe}</p>
  <p style="margin:0 0 8px">
    <a href="https://chemins-communs.fr/admin"
       style="display:inline-block;padding:10px 22px;background:#2d6a4f;color:#fff;
              text-decoration:none;border-radius:10px;font-weight:600;font-size:14px">
      Ouvrir la page admin
    </a>
  </p>
  <p style="font-size:12px;color:#888;margin:16px 0 0">
    Notification automatique — envoyée une seule fois par compte, à sa première connexion.
  </p>
"""
    return subject, _wrap_html(body)


def notify_admin_new_account(new_email: str) -> bool:
    """Tell the admin (``ADMIN_NOTIFY_EMAIL``) that a new account confirmed its
    address. No-op (False) when the variable is unset. Never raises."""
    if not ADMIN_NOTIFY_EMAIL:
        return False
    try:
        subject, html = render_admin_new_account_email(new_email)
        return send_email(ADMIN_NOTIFY_EMAIL, subject, html)
    except Exception:  # noqa: BLE001 — a notification must never break a login
        logger.warning("admin new-account notification failed", exc_info=True)
        return False

"""CLI: bootstrap (or tear down) the Strava webhook subscription.

Strava only allows ONE active push-subscription per OAuth app. This
script manages its lifecycle:

  list      — show the current subscription(s) on Strava's side
  create    — POST a new subscription pointing at our callback URL
  delete ID — remove a specific subscription by id

Usage::

    python -m app.cli.strava_subscribe list
    python -m app.cli.strava_subscribe create
    python -m app.cli.strava_subscribe delete 12345

Required env vars (read from the same Secret Manager as the api):

  STRAVA_CLIENT_ID
  STRAVA_CLIENT_SECRET
  STRAVA_WEBHOOK_CALLBACK_URL    — public HTTPS URL (e.g.
      ``https://common-trails-api-prod-…run.app/integrations/strava/webhook``)
  STRAVA_WEBHOOK_VERIFY_TOKEN    — random secret shared with the api
      so Strava's GET handshake can be authenticated

Bootstrap flow:

  1. Deploy the api with the new webhook endpoint (it already echoes
     the challenge if STRAVA_WEBHOOK_VERIFY_TOKEN is set).
  2. Run ``python -m app.cli.strava_subscribe create``.
     - Strava IMMEDIATELY GETs our callback URL with
       ``hub.mode=subscribe&hub.verify_token=…&hub.challenge=…``
     - Our endpoint responds 200 with ``{"hub.challenge": "<echo>"}``
     - Strava confirms with the new subscription_id
  3. Verify with ``... list``.

Re-running ``create`` while a subscription already exists returns 400
from Strava (one-per-app rule). Use ``delete`` first if you need to
recreate (e.g. callback URL changed).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from app.services.strava_client import STRAVA_API_BASE

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Strava's `GET /push_subscriptions` + `DELETE /push_subscriptions/{id}`
# require `client_secret` as a URL *query parameter* (Strava REST contract
# — POST is the only verb that accepts it in the body). httpx logs every
# request URL at INFO level, so leaving the default logger config would
# spill `client_secret=…` into Cloud Logging (and operator-laptop logs).
# Silence httpx + httpcore INFO chatter; we still emit our own
# (`log.info(...)`) lines which never include the secret.
# See [[project_ingestion_audit_2026_05_27]] S1.3 — the prior fix
# attempt didn't land on `main`; this is the real one.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


_PUSH_SUBS_URL = f"{STRAVA_API_BASE}/push_subscriptions"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        log.error("Env var %s is required but empty", name)
        sys.exit(2)
    return value


def cmd_list() -> int:
    import httpx

    client_id = _required("STRAVA_CLIENT_ID")
    client_secret = _required("STRAVA_CLIENT_SECRET")

    try:
        resp = httpx.get(
            _PUSH_SUBS_URL,
            params={"client_id": client_id, "client_secret": client_secret},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        # `repr(exc)` carries the URL (with `client_secret=…`) for several
        # httpx exception types (ConnectError, TimeoutException, …). Log
        # only the exception class name so transport-error tracebacks
        # don't leak the secret into Cloud Logging / operator terminals.
        log.error("Strava list transport error: %s", type(exc).__name__)
        return 1
    if resp.status_code != 200:
        log.error("Strava list failed status=%s body=%s", resp.status_code, resp.text)
        return 1
    subs = resp.json()
    print(json.dumps(subs, indent=2))
    if not subs:
        print("(no active subscription)", file=sys.stderr)
    return 0


def cmd_create() -> int:
    import httpx

    client_id = _required("STRAVA_CLIENT_ID")
    client_secret = _required("STRAVA_CLIENT_SECRET")
    callback_url = _required("STRAVA_WEBHOOK_CALLBACK_URL")
    verify_token = _required("STRAVA_WEBHOOK_VERIFY_TOKEN")

    log.info("Creating Strava webhook subscription → %s", callback_url)
    # Strava will IMMEDIATELY hit `callback_url` with a GET handshake
    # before responding to this POST. Our api must be live AND have the
    # same verify_token configured.
    # Secret is in the POST body here, NOT the URL — so transport-error
    # tracebacks don't leak it. Still wrap for symmetry with cmd_list /
    # cmd_delete which need the secret in the URL.
    try:
        resp = httpx.post(
            _PUSH_SUBS_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "callback_url": callback_url,
                "verify_token": verify_token,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        log.error("Strava subscribe transport error: %s", type(exc).__name__)
        return 1
    if resp.status_code not in (200, 201):
        log.error("Strava subscribe failed status=%s body=%s", resp.status_code, resp.text)
        # Help the operator understand the most common failure mode.
        # Strava error response is JSON like:
        #   {"message":"Bad Request","errors":[
        #       {"resource":"PushSubscription","field":"","code":"already exists"}]}
        # Parse rather than string-match the whole body (the words
        # "already" and "exists" might collide with other future error
        # messages).
        try:
            err_codes = {e.get("code", "") for e in resp.json().get("errors", [])}
        except (ValueError, AttributeError):
            err_codes = set()
        if any("already" in c.lower() and "exists" in c.lower() for c in err_codes):
            log.error(
                "Strava allows ONE subscription per app. Run "
                "'python -m app.cli.strava_subscribe list' to see the current "
                "one, then 'delete <id>' if you need to replace it."
            )
        return 1
    body = resp.json()
    sub_id = body.get("id")
    log.info("Strava subscription created id=%s", sub_id)
    print(json.dumps(body, indent=2))
    log.info(
        "Store the subscription id in Secret Manager / terraform as "
        "STRAVA_WEBHOOK_SUBSCRIPTION_ID (optional — used only for "
        "future POST-event sanity-check)."
    )
    return 0


def cmd_delete(sub_id: int) -> int:
    import httpx

    client_id = _required("STRAVA_CLIENT_ID")
    client_secret = _required("STRAVA_CLIENT_SECRET")

    log.info("Deleting Strava webhook subscription id=%s", sub_id)
    try:
        resp = httpx.delete(
            f"{_PUSH_SUBS_URL}/{sub_id}",
            params={"client_id": client_id, "client_secret": client_secret},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        log.error("Strava delete transport error: %s", type(exc).__name__)
        return 1
    if resp.status_code not in (200, 204):
        log.error("Strava delete failed status=%s body=%s", resp.status_code, resp.text)
        return 1
    log.info("Subscription %s deleted", sub_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Show current subscription(s)")
    sub.add_parser("create", help="Register a new subscription")
    p_del = sub.add_parser("delete", help="Delete a subscription by id")
    p_del.add_argument("sub_id", type=int, help="subscription id (from list)")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "create":
        return cmd_create()
    if args.cmd == "delete":
        return cmd_delete(args.sub_id)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

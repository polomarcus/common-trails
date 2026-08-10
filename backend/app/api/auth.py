"""Authentication endpoints — register, login (JWT via httpOnly cookie)."""
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import FRONTEND_URL, MAGIC_LINK_TTL_MIN, TEST_MODE
from app.db.models import MagicLinkToken, User
from app.db.session import get_db
from app.rate_limit import _get_real_ip, limiter
from app.services.email import (
    render_email_change_email,
    render_magic_link_email,
    send_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# Dummy hash for constant-time rejection when user not found (prevents email enumeration)
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode()

# ── Config ──────────────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get("JWT_SECRET", "")
_WEAK_SECRETS = {"", "dev-secret-change-in-production", "changeme", "secret"}
if JWT_SECRET in _WEAK_SECRETS:
    if TEST_MODE:
        logger.warning("JWT_SECRET is weak/default — only acceptable in TEST_MODE")
        if not JWT_SECRET:
            JWT_SECRET = "dev-only-insecure-secret"
    else:
        raise RuntimeError(
            "JWT_SECRET must be set to a strong, unique secret in production"
        )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "129600"))  # 90 days

# ── Magic-link (passwordless) config ─────────────────────────────────────────
_MAGIC_LINK_PURPOSE = "magic_link"
# Confirm-a-new-email flow (account unification). A distinct purpose so:
#  * the login rate-limit COUNT only counts magic_link rows;
#  * a magic-link login token can NEVER finalize an email change and vice-versa
#    (the confirm endpoint requires purpose == email_change);
#  * like magic_link, this purpose travels in a URL and MUST be rejected as a
#    session credential — `_decode_token` rejects any token carrying `purpose`.
_EMAIL_CHANGE_PURPOSE = "email_change"
_SYNTHETIC_EMAIL_DOMAIN = "@strava.local"
# Rate-limit: at most N magic-link requests per email AND per IP within the
# rolling window. Belt-and-suspenders with the slowapi per-IP decorator below.
_MAGIC_LINK_MAX_PER_WINDOW = 3
_MAGIC_LINK_WINDOW_MIN = 15
# Generic, non-enumerating response text (SSOT so both the success and the
# rate-limited/unknown-email paths return the exact same body).
_MAGIC_LINK_GENERIC_MSG = (
    "If an account exists for that email, a login link has been sent."
)

# ── Cookie config ───────────────────────────────────────────────────────────
_COOKIE_SECURE = os.environ.get("ENV", "production") != "development"
_COOKIE_MAX_AGE = JWT_EXPIRE_MINUTES * 60


def _set_auth_cookie(response: Response, token: str) -> None:
    """Set httpOnly auth cookie on the response."""
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=_COOKIE_MAX_AGE,
    )


def _clear_auth_cookie(response: Response) -> None:
    """Clear the auth cookie — attributes must match _set_auth_cookie for browser to delete it."""
    response.delete_cookie(
        key="auth_token",
        path="/",
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
    )

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ── Schemas ─────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    username: str
    invite_code: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserOut(BaseModel):
    user_id: str
    email: str
    username: str


class AuthenticatedUser(BaseModel):
    """Internal representation of the authenticated user, used throughout the codebase."""
    user_id: str
    email: str
    username: str
    hashed_password: str | None = None
    is_admin: bool = False


class MagicLinkRequest(BaseModel):
    email: EmailStr
    # Optional UI locale so the email is sent in the user's language.
    locale: str | None = None


class MagicLinkRequestResponse(BaseModel):
    """Deliberately generic — never reveals whether the email exists."""
    ok: bool = True
    message: str = _MAGIC_LINK_GENERIC_MSG


class MagicLinkVerifyRequest(BaseModel):
    token: str


class MagicLinkVerifyResponse(BaseModel):
    ok: bool = True
    user_id: str
    email: str
    redirect: str = "/strava"


class SetEmailRequest(BaseModel):
    email: EmailStr
    # Optional UI locale so the confirmation email is sent in the user's language.
    locale: str | None = None


class SetEmailResponse(BaseModel):
    ok: bool = True
    # True when a confirmation link was emailed to `email` and the change is
    # pending its click; False when the address already matched the account
    # (no-op). The email is NOT changed on the account until confirmation.
    pending: bool = True
    email: str


class ConfirmEmailRequest(BaseModel):
    token: str


class ConfirmEmailResponse(BaseModel):
    ok: bool = True
    user_id: str
    email: str


# ── Helpers ─────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _create_token(user_id: str, email: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": user_id, "email": email, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _normalize_email(email: str) -> str:
    """Lower-case + trim. Enough for find-or-create by email (the address's
    local part is technically case-sensitive per RFC, but in practice every
    provider treats it case-insensitively — matching that avoids duplicate
    accounts for Foo@x.com vs foo@x.com)."""
    return email.strip().lower()


def _create_magic_link_token(user_id: str, jti: str, expire: datetime) -> str:
    """Signed, short-lived, single-use login token (JWT).

    Distinct from the session token via ``purpose='magic_link'`` so a session
    JWT can never be replayed at /auth/email/verify and vice-versa.
    """
    payload = {
        "sub": user_id,
        "purpose": _MAGIC_LINK_PURPOSE,
        "jti": jti,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _create_email_change_token(
    user_id: str, new_email: str, jti: str, expire: datetime
) -> str:
    """Signed, short-lived, single-use token confirming a NEW email address.

    Carries ``purpose='email_change'`` (rejected by ``_decode_token`` as a
    session credential, exactly like the magic-link token) and the target
    ``email`` so the confirm endpoint can finalize it. Single-use is enforced by
    the ``jti`` row in ``magic_link_tokens`` (purpose='email_change').
    """
    payload = {
        "sub": user_id,
        "purpose": _EMAIL_CHANGE_PURPOSE,
        "jti": jti,
        "email": new_email,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    """Decode + validate a SESSION token (cookie or Bearer).

    SECURITY: rejects any token carrying a ``purpose`` claim. Session tokens
    minted by ``_create_token`` have none; the magic-link token
    (``purpose='magic_link'``) carries one and travels in a URL query param
    (``/auth/verify?token=…``) — a high-leak channel (browser history, Referer,
    logs). Without this guard an attacker holding a raw magic-link token could
    replay it directly as the ``auth_token`` cookie / Bearer and authenticate
    for the full TTL, bypassing the single-use ``magic_link_tokens`` ledger.
    The magic-link /verify flow decodes its token in ``verify_magic_link`` via
    its own ``jwt.decode`` — NOT through this function — so this never blocks it.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if payload.get("purpose") is not None:
        # A non-session token (e.g. magic_link) must never authenticate a session.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def _user_to_auth(user: User) -> AuthenticatedUser:
    """Convert a User ORM object to the typed AuthenticatedUser model."""
    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        username=user.username,
        hashed_password=user.hashed_password,
        is_admin=getattr(user, "is_admin", False),
    )


def user_exists(user_id: str, db: Session) -> bool:
    """Check if a user exists in the database."""
    return db.query(User.id).filter(User.id == user_id).first() is not None


oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthenticatedUser:
    """Get current user from Bearer token or httpOnly auth cookie."""
    effective_token = token or request.cookies.get("auth_token")
    if not effective_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decode_token(effective_token)
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _user_to_auth(user)


async def get_current_user_optional(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthenticatedUser | None:
    """Like get_current_user but returns None if no/invalid token (no 401)."""
    effective_token = token or request.cookies.get("auth_token")
    if not effective_token:
        return None
    try:
        return await get_current_user(request, token, db)
    except HTTPException:
        return None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=LoginResponse, status_code=201)
@limiter.limit("5/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> LoginResponse:
    """Create a new user account.

    Closed beta gate: if env BETA_INVITE_CODE is set (non-empty), the
    request must include a matching invite_code. When the env is unset
    (default for dev / open beta), registration stays open to anyone.
    """
    expected_invite = os.environ.get("BETA_INVITE_CODE", "").strip()
    if expected_invite:
        provided = (body.invite_code or "").strip()
        # secrets.compare_digest = constant-time, avoids char-by-char timing leak.
        # Encode to bytes to compare same length safely.
        if not secrets.compare_digest(
            provided.encode("utf-8"),
            expected_invite.encode("utf-8"),
        ):
            raise HTTPException(
                status_code=403,
                detail="Beta access required — invite code missing or invalid",
            )

    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email=body.email,
        username=body.username,
        hashed_password=_hash_password(body.password),
    )
    db.add(user)
    db.commit()

    token = _create_token(user_id, body.email)
    _set_auth_cookie(response, token)
    return LoginResponse(access_token=token, user_id=user_id, email=body.email)


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> LoginResponse:
    """Authenticate with email + password, return JWT."""
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not user.hashed_password:
        # Constant-time rejection: run bcrypt even when user not found (prevents timing-based email enumeration)
        bcrypt.checkpw(b"dummy", _DUMMY_HASH.encode())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_token(user.id, user.email)
    _set_auth_cookie(response, token)
    return LoginResponse(access_token=token, user_id=user.id, email=user.email)


@router.delete("/logout", status_code=204)
async def logout(response: Response) -> None:
    """Clear the auth cookie."""
    _clear_auth_cookie(response)


@router.post("/email/request", response_model=MagicLinkRequestResponse)
@limiter.limit("3/15minute")
async def request_magic_link(
    request: Request,
    body: MagicLinkRequest,
    db: Annotated[Session, Depends(get_db)],
) -> MagicLinkRequestResponse:
    """Passwordless login step 1 — email a single-use magic link.

    Security posture:
    * ALWAYS returns the same generic 200 body — never reveals whether an
      account exists (no enumeration).
    * Rate-limited per IP (the slowapi decorator) AND per email + per IP in the
      DB (``_MAGIC_LINK_MAX_PER_WINDOW`` per ``_MAGIC_LINK_WINDOW_MIN`` min).
      When over the limit we still return the generic 200 but send nothing.
    * find-or-create: a first-time email becomes a full account.
    """
    email = _normalize_email(body.email)
    ip = _get_real_ip(request)
    generic = MagicLinkRequestResponse()

    now = datetime.now(UTC)
    window_start = now - timedelta(minutes=_MAGIC_LINK_WINDOW_MIN)

    # Rate-limit by email OR IP (DB-backed, testable regardless of slowapi).
    recent = (
        db.query(MagicLinkToken)
        .filter(
            MagicLinkToken.purpose == _MAGIC_LINK_PURPOSE,
            MagicLinkToken.created_at >= window_start,
            (MagicLinkToken.email == email) | (MagicLinkToken.request_ip == ip),
        )
        .count()
    )
    if recent >= _MAGIC_LINK_MAX_PER_WINDOW:
        logger.info("Magic-link rate-limited (email/ip) — returning generic 200")
        return generic

    # find-or-create the user by email. A magic-link account has no password.
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            username=email.split("@")[0][:100] or "cycliste",
        )
        db.add(user)
        db.flush()  # populate user.id without ending the transaction

    jti = str(uuid.uuid4())
    expire = now + timedelta(minutes=MAGIC_LINK_TTL_MIN)
    token = _create_magic_link_token(user.id, jti, expire)

    db.add(MagicLinkToken(
        jti=jti,
        user_id=user.id,
        email=email,
        request_ip=ip,
        created_at=now,
        expires_at=expire,
        purpose=_MAGIC_LINK_PURPOSE,
    ))
    db.commit()

    # Send the email — send_email() never raises and no-ops in TEST_MODE, so
    # the generic 200 is returned regardless of the email backend's health.
    locale = (body.locale or "fr").lower()
    verify_url = f"{FRONTEND_URL}/auth/verify?token={token}"
    subject, html = render_magic_link_email(verify_url, locale=locale)
    send_email(email, subject, html)

    return generic


@router.post("/email/verify", response_model=MagicLinkVerifyResponse)
@limiter.limit("10/minute")
async def verify_magic_link(
    request: Request,
    body: MagicLinkVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> MagicLinkVerifyResponse:
    """Passwordless login step 2 — consume the magic link, issue a session.

    Verifies signature + TTL + ``purpose`` + jti-not-yet-consumed. On success
    marks the jti consumed (single-use) and sets the SAME httpOnly session
    cookie as /auth/login. Any failure → 401 with a non-enumerating message.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="This login link is invalid or has expired. Please request a new one.",
    )

    # 1. Signature + exp (jose raises JWTError on bad sig / expiry).
    try:
        payload = jwt.decode(body.token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise invalid from exc

    # 2. Correct purpose — a session JWT must never be replayable here.
    if payload.get("purpose") != _MAGIC_LINK_PURPOSE:
        raise invalid
    jti = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise invalid

    # 3. Single-use consume — ATOMIC. One conditional UPDATE does the
    # read-check-write in a single statement: it flips consumed_at only if the
    # row is still unconsumed AND unexpired. This closes the race where two
    # concurrent verifies of the same token both pass a separate `consumed_at
    # IS NULL` read and both issue a session. rowcount 0 ⇒ unknown jti / already
    # consumed / expired ⇒ reject. RETURNING gives us the authoritative
    # user_id from the row (must also match the JWT sub).
    consumed = db.execute(
        sa_text(
            "UPDATE magic_link_tokens SET consumed_at = now() "
            "WHERE jti = :jti AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING user_id"
        ),
        {"jti": jti},
    ).first()
    db.commit()
    if consumed is None:
        raise invalid
    row_user_id = consumed[0]
    if row_user_id != user_id:
        raise invalid

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise invalid

    session_token = _create_token(user.id, user.email)
    _set_auth_cookie(response, session_token)
    return MagicLinkVerifyResponse(user_id=user.id, email=user.email)


@router.get("/me", response_model=UserOut)
@limiter.limit("60/minute")
async def me(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> UserOut:
    """Return current authenticated user info."""
    return UserOut(
        user_id=current_user.user_id,
        email=current_user.email,
        username=current_user.username,
    )


@router.post("/me/email", response_model=SetEmailResponse)
@limiter.limit("5/15minute")
async def request_email_change(
    request: Request,
    body: SetEmailRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> SetEmailResponse:
    """Set / change the current user's email — CONFIRMED via a magic link.

    The primary use is account unification: a Strava-OAuth user whose email is
    still synthetic (``strava_<id>@strava.local``) supplies their REAL address
    so they can log in by email and receive notifications. It also handles a
    plain email change for any account.

    SECURITY — no account takeover:
      * If the target email already belongs to ANOTHER user → 409. We NEVER
        merge or reassign accounts; the caller is told to log in with the email
        login instead. The DB unique constraint on ``users.email`` is the
        backstop (finalize re-checks + would fail the insert otherwise).
      * We do NOT trust the address blindly: instead of writing it immediately,
        we email a single-use confirmation link (purpose=``email_change``) to
        the NEW address and only finalize on ``POST /auth/me/email/confirm``.
        Until then the account keeps its current address — an unverified or
        mistyped address is never bound, and you can't claim an address you
        don't control.
    """
    new_email = _normalize_email(body.email)

    user = db.query(User).filter(User.id == current_user.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    # No-op when the address already matches (idempotent, no email sent).
    if user.email == new_email:
        return SetEmailResponse(ok=True, pending=False, email=new_email)

    # Takeover guard: refuse an address already owned by a different account.
    other = (
        db.query(User)
        .filter(User.email == new_email, User.id != user.id)
        .first()
    )
    if other:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cet email est déjà utilisé — connecte-toi avec le login email."
            ),
        )

    now = datetime.now(UTC)
    jti = str(uuid.uuid4())
    expire = now + timedelta(minutes=MAGIC_LINK_TTL_MIN)
    token = _create_email_change_token(user.id, new_email, jti, expire)

    db.add(MagicLinkToken(
        jti=jti,
        user_id=user.id,
        email=new_email,  # the NEW (target) address to finalize on confirm
        request_ip=_get_real_ip(request),
        created_at=now,
        expires_at=expire,
        purpose=_EMAIL_CHANGE_PURPOSE,
    ))
    db.commit()

    locale = (body.locale or "fr").lower()
    confirm_url = f"{FRONTEND_URL}/auth/confirm-email?token={token}"
    subject, html = render_email_change_email(confirm_url, locale=locale)
    send_email(new_email, subject, html)

    return SetEmailResponse(ok=True, pending=True, email=new_email)


@router.post("/me/email/confirm", response_model=ConfirmEmailResponse)
@limiter.limit("10/minute")
async def confirm_email_change(
    request: Request,
    body: ConfirmEmailRequest,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> ConfirmEmailResponse:
    """Finalize an email change — consume the confirmation link.

    Verifies signature + TTL + ``purpose=email_change`` + jti-not-yet-consumed,
    then writes the new address to the account. No active session is required
    (the token itself authorizes the change for its ``sub``), so the link works
    from any device — same posture as the magic-link login. Re-checks the
    takeover guard at finalize time in case the address was claimed between
    request and confirm; the DB unique constraint is the final backstop.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="This confirmation link is invalid or has expired. Please request a new one.",
    )

    try:
        payload = jwt.decode(body.token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise invalid from exc

    if payload.get("purpose") != _EMAIL_CHANGE_PURPOSE:
        raise invalid
    jti = payload.get("jti")
    user_id = payload.get("sub")
    new_email = payload.get("email")
    if not jti or not user_id or not new_email:
        raise invalid

    # Atomic single-use consume — flips consumed_at only if still unconsumed,
    # unexpired AND an email_change row (a magic_link login jti can't be
    # replayed here). RETURNING binds us to the row's authoritative fields.
    consumed = db.execute(
        sa_text(
            "UPDATE magic_link_tokens SET consumed_at = now() "
            "WHERE jti = :jti AND consumed_at IS NULL AND expires_at > now() "
            "AND purpose = :purpose "
            "RETURNING user_id, email"
        ),
        {"jti": jti, "purpose": _EMAIL_CHANGE_PURPOSE},
    ).first()
    db.commit()
    if consumed is None:
        raise invalid
    row_user_id, row_email = consumed[0], consumed[1]
    if row_user_id != user_id or row_email != new_email:
        raise invalid

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise invalid

    # Re-check takeover at finalize time (address may have been claimed since
    # the request). Never merge — refuse.
    other = (
        db.query(User)
        .filter(User.email == new_email, User.id != user.id)
        .first()
    )
    if other:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet email est déjà utilisé — connecte-toi avec le login email.",
        )

    user.email = new_email
    db.commit()

    # Refresh the session cookie so the email claim in the JWT stays coherent.
    session_token = _create_token(user.id, user.email)
    _set_auth_cookie(response, session_token)
    return ConfirmEmailResponse(ok=True, user_id=user.id, email=user.email)

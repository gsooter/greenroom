"""Authentication service — magic-link, Google OAuth, Apple OAuth, and
WebAuthn passkey flows.

Routes stay thin; all auth business logic lives here. The module imports
its collaborators by name (``magic_links_repo``, ``users_repo``,
``email_service``, ``requests``, ``webauthn``) so tests can
``monkeypatch.setattr`` them without patching import machinery.
"""

import base64
import hashlib
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
import webauthn
from sqlalchemy.orm import Session
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from backend.core.auth import issue_token
from backend.core.config import get_settings
from backend.core.exceptions import (
    APPLE_AUTH_FAILED,
    GOOGLE_AUTH_FAILED,
    MAGIC_LINK_ALREADY_USED,
    MAGIC_LINK_EXPIRED,
    MAGIC_LINK_INVALID,
    PASSKEY_AUTH_FAILED,
    PASSKEY_REGISTRATION_FAILED,
    AppError,
    ValidationError,
)
from backend.core.logging import get_logger
from backend.data.models.users import OAuthProvider, User
from backend.data.repositories import magic_links as magic_links_repo
from backend.data.repositories import passkeys as passkeys_repo
from backend.data.repositories import users as users_repo
from backend.services import email as email_service

logger = get_logger(__name__)

_RAW_TOKEN_BYTES = 32

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPES = "openid email profile"

_APPLE_AUTHORIZE_URL = "https://appleid.apple.com/auth/authorize"
_APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_ISSUER = "https://appleid.apple.com"
_APPLE_SCOPES = "name email"
_APPLE_CLIENT_SECRET_TTL_SECONDS = 180 * 24 * 60 * 60  # Apple caps at 6 months.


@dataclass(frozen=True)
class MagicLinkDelivery:
    """Return value from :func:`generate_magic_link`.

    Attributes:
        raw_token: The unhashed token that rode in the outgoing email.
            Exposed to the caller only so tests can assert on it; a
            production caller discards it immediately after the email
            is sent.
        expires_at: Wall-clock UTC expiry of the issued token.
    """

    raw_token: str
    expires_at: datetime


@dataclass(frozen=True)
class MagicLinkVerification:
    """Return value from :func:`verify_magic_link`.

    Attributes:
        user: The authenticated user — either freshly created on first
            visit or the pre-existing row for a returning user.
        jwt: A signed session token the client stores and presents on
            subsequent API calls.
    """

    user: User
    jwt: str


def generate_magic_link(session: Session, *, email: str) -> MagicLinkDelivery:
    """Mint a magic-link token for ``email`` and send it via SendGrid.

    Only the SHA-256 hash of the raw token is persisted (Decision 027);
    the raw value leaves the process inside the email body and is
    returned to the caller so it can be asserted on in tests and logged
    behind a debug flag if needed.

    Args:
        session: Active SQLAlchemy session.
        email: Destination address. Casing is normalized before storage
            so the lookup key is stable across clients that upper- or
            title-case addresses.

    Returns:
        A :class:`MagicLinkDelivery` with the raw token and its expiry.

    Raises:
        ValidationError: If the email is empty or whitespace-only.
        AppError: ``EMAIL_DELIVERY_FAILED`` if SendGrid rejects the send.
    """
    cleaned = email.strip().lower()
    if not cleaned:
        raise ValidationError(message="An email address is required.")

    settings = get_settings()
    raw_token = secrets.token_urlsafe(_RAW_TOKEN_BYTES)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.magic_link_ttl_seconds)

    magic_links_repo.create(
        session,
        email=cleaned,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    verify_url = (
        f"{settings.frontend_base_url.rstrip('/')}/auth/verify?token={raw_token}"
    )
    html_body = _render_email_html(verify_url=verify_url)
    email_service.send_email(
        to=cleaned,
        subject="Your Greenroom sign-in link",
        html_body=html_body,
    )

    return MagicLinkDelivery(raw_token=raw_token, expires_at=expires_at)


def verify_magic_link(session: Session, *, token: str) -> MagicLinkVerification:
    """Redeem a magic-link token and return the authenticated user + JWT.

    Looks up the token by its hash, checks freshness and single-use,
    finds-or-creates the user bound to the token's email, stamps the
    token as used, refreshes ``last_login_at``, and signs a JWT.

    Args:
        session: Active SQLAlchemy session.
        token: The raw token from the email URL.

    Returns:
        A :class:`MagicLinkVerification` with the user and a fresh JWT.

    Raises:
        AppError: ``MAGIC_LINK_INVALID`` when no matching token exists
            or the bound user is deactivated;
            ``MAGIC_LINK_EXPIRED`` when ``expires_at`` has passed;
            ``MAGIC_LINK_ALREADY_USED`` when ``used_at`` is already set.
    """
    row = magic_links_repo.get_by_hash(session, _hash_token(token))
    if row is None:
        raise AppError(
            code=MAGIC_LINK_INVALID,
            message="This sign-in link is not valid.",
            status_code=400,
        )
    if row.used_at is not None:
        raise AppError(
            code=MAGIC_LINK_ALREADY_USED,
            message="This sign-in link has already been used.",
            status_code=400,
        )
    if row.expires_at <= datetime.now(UTC):
        raise AppError(
            code=MAGIC_LINK_EXPIRED,
            message="This sign-in link has expired.",
            status_code=400,
        )

    email = row.email.lower()
    user = users_repo.get_user_by_email(session, email)
    if user is None:
        user = users_repo.create_user(session, email=email)
    elif not user.is_active:
        raise AppError(
            code=MAGIC_LINK_INVALID,
            message="This account is no longer active.",
            status_code=400,
        )

    magic_links_repo.mark_used(session, row, user_id=user.id)
    users_repo.update_last_login(session, user)

    return MagicLinkVerification(user=user, jwt=issue_token(user.id))


def _hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest used for at-rest token storage.

    Args:
        raw_token: The unhashed token string.

    Returns:
        Lowercase hex digest suitable for the ``token_hash`` column.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthLogin:
    """Return value from OAuth ``*_complete`` entry points.

    Attributes:
        user: The authenticated user (created or found during upsert).
        jwt: A signed Greenroom session token.
    """

    user: User
    jwt: str


def google_build_authorize_url(*, state: str) -> str:
    """Return the Google OAuth consent URL with ``state`` embedded.

    The frontend navigates the browser to this URL; Google redirects
    back to :data:`Settings.google_oauth_redirect_uri` with ``code`` and
    ``state`` query parameters, which the callback page POSTs to
    :func:`google_complete`.

    Args:
        state: CSRF-prevention nonce the caller minted (typically a
            short-lived signed JWT). Round-trips unchanged.

    Returns:
        Fully qualified Google authorize URL.
    """
    settings = get_settings()
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": _GOOGLE_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{_GOOGLE_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def google_complete(session: Session, *, code: str) -> OAuthLogin:
    """Exchange a Google OAuth ``code`` for a Greenroom session.

    Args:
        session: Active SQLAlchemy session.
        code: Authorization code Google put in the redirect.

    Returns:
        An :class:`OAuthLogin` with the authenticated user and JWT.

    Raises:
        AppError: ``GOOGLE_AUTH_FAILED`` for any failure in the token
            exchange, userinfo fetch, or if Google reports the email
            as unverified.
    """
    tokens = _google_exchange_code(code)
    profile = _google_fetch_profile(tokens["access_token"])

    if not profile.get("email_verified"):
        raise AppError(
            code=GOOGLE_AUTH_FAILED,
            message="Google reported this email as unverified.",
            status_code=400,
        )

    expires_at: datetime | None = None
    if isinstance(tokens.get("expires_in"), int):
        expires_at = datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))

    user = _upsert_oauth_user(
        session,
        provider=OAuthProvider.GOOGLE,
        provider_user_id=str(profile["sub"]),
        email=str(profile["email"]).lower(),
        display_name=profile.get("name"),
        avatar_url=profile.get("picture"),
        access_token=str(tokens["access_token"]),
        refresh_token=tokens.get("refresh_token"),
        token_expires_at=expires_at,
    )
    return OAuthLogin(user=user, jwt=issue_token(user.id))


def _google_exchange_code(code: str) -> dict[str, Any]:
    """POST ``code`` to Google's token endpoint and return the token dict.

    Args:
        code: Authorization code from Google's redirect.

    Returns:
        JSON body from Google's token endpoint.

    Raises:
        AppError: ``GOOGLE_AUTH_FAILED`` on any non-200 response.
    """
    settings = get_settings()
    resp = requests.post(
        _GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        logger.warning("google_token_exchange_failed: status=%s", resp.status_code)
        raise AppError(
            code=GOOGLE_AUTH_FAILED,
            message="Google rejected the authorization code.",
            status_code=400,
        )
    return dict(resp.json())


def _google_fetch_profile(access_token: str) -> dict[str, Any]:
    """Fetch the Google userinfo payload for the given access token.

    Args:
        access_token: Google OAuth access token.

    Returns:
        JSON body from Google's userinfo endpoint.

    Raises:
        AppError: ``GOOGLE_AUTH_FAILED`` on any non-200 response.
    """
    resp = requests.get(
        _GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.warning("google_userinfo_failed: status=%s", resp.status_code)
        raise AppError(
            code=GOOGLE_AUTH_FAILED,
            message="Could not load Google profile.",
            status_code=400,
        )
    return dict(resp.json())


# ---------------------------------------------------------------------------
# Apple Sign-in
# ---------------------------------------------------------------------------


def apple_build_authorize_url(*, state: str) -> str:
    """Return the Apple sign-in URL with ``state`` embedded.

    Apple requires ``response_mode=form_post`` when scopes include
    ``name`` or ``email``; the frontend's callback page translates the
    POSTed form into a JSON body before calling
    :func:`apple_complete`.

    Args:
        state: CSRF-prevention nonce the caller minted.

    Returns:
        Fully qualified Apple authorize URL.
    """
    settings = get_settings()
    params = {
        "client_id": settings.apple_oauth_client_id,
        "redirect_uri": settings.apple_oauth_redirect_uri,
        "response_type": "code",
        "response_mode": "form_post",
        "scope": _APPLE_SCOPES,
        "state": state,
    }
    return f"{_APPLE_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def apple_complete(
    session: Session,
    *,
    code: str,
    user_data: dict[str, Any] | None,
) -> OAuthLogin:
    """Exchange an Apple OAuth ``code`` for a Greenroom session.

    Apple's quirks handled here:

    - ``user_data`` is only present on the first sign-in. When it is,
      we read the display name from it because id_token claims never
      include a name.
    - ``is_private_email=true`` accounts forward to the real address;
      they are accepted as first-class identities.

    Args:
        session: Active SQLAlchemy session.
        code: Authorization code Apple put in the redirect form.
        user_data: Optional ``user`` payload Apple POSTs on first sign-in.

    Returns:
        An :class:`OAuthLogin` with the authenticated user and JWT.

    Raises:
        AppError: ``APPLE_AUTH_FAILED`` for any failure in the token
            exchange or id-token verification.
    """
    client_secret = _apple_mint_client_secret()
    tokens = _apple_exchange_code(code, client_secret)

    id_token = tokens.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Apple did not return an id token.",
            status_code=400,
        )

    profile = _apple_verify_id_token(id_token)

    # Apple encodes ``email_verified`` as a string ("true" / "false")
    # in id_token claims, so coerce before comparing. A false value
    # on a non-relay address is unusual but possible.
    email_verified = str(profile.get("email_verified", "")).lower() == "true"
    is_relay = str(profile.get("is_private_email", "")).lower() == "true"
    if not email_verified and not is_relay:
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Apple reported this email as unverified.",
            status_code=400,
        )

    display_name = _apple_display_name(user_data)
    email_raw = profile.get("email")
    if not isinstance(email_raw, str) or not email_raw:
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Apple did not return an email address.",
            status_code=400,
        )

    user = _upsert_oauth_user(
        session,
        provider=OAuthProvider.APPLE,
        provider_user_id=str(profile["sub"]),
        email=email_raw.lower(),
        display_name=display_name,
        avatar_url=None,
        access_token=str(tokens.get("access_token") or ""),
        refresh_token=tokens.get("refresh_token"),
        token_expires_at=None,
    )
    return OAuthLogin(user=user, jwt=issue_token(user.id))


def _apple_display_name(user_data: dict[str, Any] | None) -> str | None:
    """Pull a user-visible name from Apple's first-sign-in ``user`` payload.

    Args:
        user_data: Apple's ``user`` form field parsed as JSON, or None.

    Returns:
        A display string, or None if nothing usable was provided.
    """
    if not isinstance(user_data, dict):
        return None
    name = user_data.get("name")
    if not isinstance(name, dict):
        return None
    parts = [
        str(name.get("firstName") or "").strip(),
        str(name.get("lastName") or "").strip(),
    ]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _apple_mint_client_secret() -> str:
    """Mint a fresh ES256 client secret JWT for Apple's token endpoint.

    Apple uses a rotating client secret signed with the team's private
    key (``.p8``). This helper signs a short-lived token; it is split
    out as a module-level function so tests can patch it without
    supplying a real key.

    Returns:
        A signed JWT suitable as ``client_secret`` in the token POST.

    Raises:
        AppError: ``APPLE_AUTH_FAILED`` if the private key is missing
            or malformed.
    """
    import jwt as _jwt

    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "iss": settings.apple_oauth_team_id,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=_APPLE_CLIENT_SECRET_TTL_SECONDS)).timestamp()
        ),
        "aud": _APPLE_ISSUER,
        "sub": settings.apple_oauth_client_id,
    }
    headers = {"kid": settings.apple_oauth_key_id, "alg": "ES256"}
    try:
        return _jwt.encode(
            claims,
            settings.apple_oauth_private_key,
            algorithm="ES256",
            headers=headers,
        )
    except Exception as exc:
        logger.warning("apple_client_secret_mint_failed: %s", exc)
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Could not mint Apple client secret.",
            status_code=500,
        ) from exc


def _apple_exchange_code(code: str, client_secret: str) -> dict[str, Any]:
    """POST ``code`` to Apple's token endpoint and return the JSON body.

    Args:
        code: Authorization code Apple supplied in the redirect.
        client_secret: ES256-signed JWT from :func:`_apple_mint_client_secret`.

    Returns:
        JSON body from Apple's token endpoint.

    Raises:
        AppError: ``APPLE_AUTH_FAILED`` on any non-200 response.
    """
    settings = get_settings()
    resp = requests.post(
        _APPLE_TOKEN_URL,
        data={
            "client_id": settings.apple_oauth_client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.apple_oauth_redirect_uri,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        logger.warning("apple_token_exchange_failed: status=%s", resp.status_code)
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Apple rejected the authorization code.",
            status_code=400,
        )
    return dict(resp.json())


def _apple_verify_id_token(id_token: str) -> dict[str, Any]:
    """Verify Apple's id_token signature and audience, returning its claims.

    Uses PyJWT's JWK client to fetch and cache Apple's public keys from
    :data:`_APPLE_JWKS_URL` and validates the signature, issuer, and
    audience.

    Args:
        id_token: Apple's id_token string.

    Returns:
        Decoded claims dictionary.

    Raises:
        AppError: ``APPLE_AUTH_FAILED`` for any verification failure.
    """
    import jwt as _jwt
    from jwt import PyJWKClient

    settings = get_settings()
    try:
        jwks_client = PyJWKClient(_APPLE_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        return dict(
            _jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.apple_oauth_client_id,
                issuer=_APPLE_ISSUER,
            )
        )
    except Exception as exc:
        logger.warning("apple_id_token_verify_failed: %s", exc)
        raise AppError(
            code=APPLE_AUTH_FAILED,
            message="Apple id token could not be verified.",
            status_code=400,
        ) from exc


# ---------------------------------------------------------------------------
# Shared OAuth upsert
# ---------------------------------------------------------------------------


def _upsert_oauth_user(
    session: Session,
    *,
    provider: OAuthProvider,
    provider_user_id: str,
    email: str,
    display_name: str | None,
    avatar_url: str | None,
    access_token: str,
    refresh_token: str | None,
    token_expires_at: datetime | None,
) -> User:
    """Find-or-create the user bound to an OAuth identity and refresh tokens.

    Resolution order:

    1. ``(provider, provider_user_id)`` — exact existing link.
    2. Matching email address on a user row — link this provider to it.
    3. No match — create a fresh user and the oauth row.

    Rejects deactivated accounts at each branch with
    :class:`AppError` using the caller's provider-specific code.

    Args:
        session: Active SQLAlchemy session.
        provider: Which :class:`OAuthProvider` this login came from.
        provider_user_id: Stable user id on the provider side.
        email: Lowercased email returned by the provider.
        display_name: Best-effort display name, may be None.
        avatar_url: Best-effort avatar URL, may be None.
        access_token: Current access token from the provider.
        refresh_token: Optional refresh token to rotate.
        token_expires_at: Optional expiry for the access token.

    Returns:
        The :class:`User` now linked to this provider.

    Raises:
        AppError: ``GOOGLE_AUTH_FAILED`` (or the future Apple equivalent)
            if the bound user is deactivated.
    """
    fail_code = _oauth_fail_code(provider)

    existing_oauth = users_repo.get_oauth_provider(
        session,
        provider=provider,
        provider_user_id=provider_user_id,
    )
    if existing_oauth is not None:
        if not existing_oauth.user.is_active:
            raise AppError(
                code=fail_code,
                message="This account is no longer active.",
                status_code=400,
            )
        users_repo.update_oauth_tokens(
            session,
            existing_oauth,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
        )
        user = existing_oauth.user
        users_repo.update_user(
            session,
            user,
            display_name=display_name or user.display_name,
            avatar_url=avatar_url or user.avatar_url,
        )
        users_repo.update_last_login(session, user)
        return user

    found = users_repo.get_user_by_email(session, email)
    if found is not None:
        if not found.is_active:
            raise AppError(
                code=fail_code,
                message="This account is no longer active.",
                status_code=400,
            )
        users_repo.update_user(
            session,
            found,
            display_name=display_name or found.display_name,
            avatar_url=avatar_url or found.avatar_url,
        )
        user = found
    else:
        user = users_repo.create_user(
            session,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
        )

    users_repo.create_oauth_provider(
        session,
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
    )
    users_repo.update_last_login(session, user)
    return user


def _oauth_fail_code(provider: OAuthProvider) -> str:
    """Map an OAuth provider to its user-facing error code.

    Args:
        provider: The provider in play.

    Returns:
        The matching ``*_AUTH_FAILED`` constant from
        :mod:`backend.core.exceptions`.
    """
    from backend.core.exceptions import SPOTIFY_AUTH_FAILED

    return {
        OAuthProvider.GOOGLE: GOOGLE_AUTH_FAILED,
        OAuthProvider.APPLE: APPLE_AUTH_FAILED,
        OAuthProvider.SPOTIFY: SPOTIFY_AUTH_FAILED,
    }.get(provider, GOOGLE_AUTH_FAILED)


# ---------------------------------------------------------------------------
# WebAuthn passkeys
# ---------------------------------------------------------------------------

_PASSKEY_CHALLENGE_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class PasskeyRegistrationChallenge:
    """A registration ceremony's public options plus its opaque state.

    Attributes:
        options: JSON-ready ``PublicKeyCredentialCreationOptions`` the
            browser passes to ``navigator.credentials.create``.
        state: Short-lived signed JWT that the client must echo back on
            the complete call. Contains the challenge and user id.
    """

    options: dict[str, Any]
    state: str


@dataclass(frozen=True)
class PasskeyAuthenticationChallenge:
    """An authentication ceremony's public options plus its opaque state.

    Attributes:
        options: JSON-ready ``PublicKeyCredentialRequestOptions`` for
            ``navigator.credentials.get``.
        state: Short-lived signed JWT that the client echoes back.
    """

    options: dict[str, Any]
    state: str


def passkey_registration_options(
    session: Session, *, user: User
) -> PasskeyRegistrationChallenge:
    """Build the public-key options + state token for registering a passkey.

    The ceremony is "user-verifying, platform-preferred": we ask for a
    resident key (the passkey is stored on the authenticator itself)
    because that's what makes the authentication side usernameless.

    Args:
        session: Active SQLAlchemy session. Used only to look up the
            user's existing credentials so we can set
            ``excludeCredentials`` (prevents double-registering the same
            authenticator).
        user: The signed-in user adding the passkey to their account.

    Returns:
        A :class:`PasskeyRegistrationChallenge` carrying the browser
        options and the signed challenge-state token.
    """
    import json as _json

    settings = get_settings()
    existing = passkeys_repo.list_by_user(session, user.id)
    opts = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.display_name or user.email,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=_b64url_decode(c.credential_id))
            for c in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    challenge_b64 = _b64url_encode(opts.challenge)
    state = _issue_passkey_state(
        purpose="passkey_register_challenge",
        claims={"user_id": str(user.id), "challenge": challenge_b64},
    )
    return PasskeyRegistrationChallenge(
        options=_json.loads(webauthn.options_to_json(opts)),
        state=state,
    )


def passkey_register_complete(
    session: Session,
    *,
    user: User,
    credential: dict[str, Any],
    state: str,
    name: str | None = None,
) -> User:
    """Verify the authenticator's attestation and persist the new credential.

    Args:
        session: Active SQLAlchemy session.
        user: The signed-in user registering the passkey.
        credential: The ``PublicKeyCredential`` JSON the browser returned
            from ``navigator.credentials.create``.
        state: The signed state token this module issued at the start of
            the ceremony.
        name: Optional user-visible label for the credential (e.g.
            "iPhone 15" or "YubiKey").

    Returns:
        The user after the credential has been recorded.

    Raises:
        AppError: ``PASSKEY_REGISTRATION_FAILED`` if the state is
            tampered with, the challenge doesn't match, or the library
            rejects the attestation.
    """
    claims = _verify_passkey_state(state, expected_purpose="passkey_register_challenge")
    if claims.get("user_id") != str(user.id):
        raise AppError(
            code=PASSKEY_REGISTRATION_FAILED,
            message="Passkey state does not match the signed-in user.",
            status_code=400,
        )
    settings = get_settings()
    try:
        verification = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=_b64url_decode(claims["challenge"]),
            expected_origin=settings.webauthn_origin,
            expected_rp_id=settings.webauthn_rp_id,
        )
    except Exception as exc:
        raise AppError(
            code=PASSKEY_REGISTRATION_FAILED,
            message="Passkey registration could not be verified.",
            status_code=400,
        ) from exc

    passkeys_repo.create(
        session,
        user_id=user.id,
        credential_id=_b64url_encode(verification.credential_id),
        public_key=_b64url_encode(verification.credential_public_key),
        transports=_transports_from_credential(credential),
        name=name,
        sign_count=verification.sign_count,
    )
    return user


def passkey_authentication_options() -> PasskeyAuthenticationChallenge:
    """Build public-key options for a usernameless passkey sign-in.

    With ``allowCredentials=[]`` the browser surfaces every resident
    passkey bound to the relying party, so the user picks the account
    themselves. The ``userHandle`` that comes back in the assertion is
    how we look up the Greenroom user.

    Returns:
        A :class:`PasskeyAuthenticationChallenge` with options + state.
    """
    import json as _json

    settings = get_settings()
    opts = webauthn.generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    challenge_b64 = _b64url_encode(opts.challenge)
    state = _issue_passkey_state(
        purpose="passkey_auth_challenge",
        claims={"challenge": challenge_b64},
    )
    return PasskeyAuthenticationChallenge(
        options=_json.loads(webauthn.options_to_json(opts)),
        state=state,
    )


def passkey_authenticate_complete(
    session: Session,
    *,
    credential: dict[str, Any],
    state: str,
) -> OAuthLogin:
    """Verify an authentication assertion and issue a Greenroom JWT.

    Args:
        session: Active SQLAlchemy session.
        credential: The ``PublicKeyCredential`` JSON from
            ``navigator.credentials.get``.
        state: The signed state token issued at ceremony start.

    Returns:
        An :class:`OAuthLogin` carrying the authenticated user and JWT.

    Raises:
        AppError: ``PASSKEY_AUTH_FAILED`` for any verification failure,
            unknown credential id, deactivated user, or sign-count
            regression (a signal the authenticator may be cloned).
    """
    claims = _verify_passkey_state(state, expected_purpose="passkey_auth_challenge")

    raw_id = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(raw_id, str) or not raw_id:
        raise AppError(
            code=PASSKEY_AUTH_FAILED,
            message="Passkey assertion is missing a credential id.",
            status_code=400,
        )
    stored = passkeys_repo.get_by_credential_id(session, raw_id)
    if stored is None:
        raise AppError(
            code=PASSKEY_AUTH_FAILED,
            message="Unknown passkey. Register it on this device first.",
            status_code=400,
        )
    if not stored.user.is_active:
        raise AppError(
            code=PASSKEY_AUTH_FAILED,
            message="This account is no longer active.",
            status_code=400,
        )

    settings = get_settings()
    try:
        verification = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=_b64url_decode(claims["challenge"]),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=_b64url_decode(stored.public_key),
            credential_current_sign_count=stored.sign_count,
        )
    except Exception as exc:
        raise AppError(
            code=PASSKEY_AUTH_FAILED,
            message="Passkey authentication could not be verified.",
            status_code=400,
        ) from exc

    passkeys_repo.update_sign_count(
        session, stored, new_count=verification.new_sign_count
    )
    users_repo.update_last_login(session, stored.user)
    return OAuthLogin(user=stored.user, jwt=issue_token(stored.user.id))


def _issue_passkey_state(*, purpose: str, claims: dict[str, Any]) -> str:
    """Mint a short-lived signed JWT capturing a passkey ceremony's challenge.

    Args:
        purpose: The ``purpose`` claim — guards against cross-ceremony
            replay (e.g. a register state being presented on an auth
            complete).
        claims: Additional claims to embed (challenge, user_id, ...).

    Returns:
        A JWT signed with the app's HS256 secret.
    """
    import jwt as _jwt

    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "purpose": purpose,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=_PASSKEY_CHALLENGE_TTL_SECONDS)).timestamp()
        ),
        "nonce": secrets.token_urlsafe(8),
        **claims,
    }
    return _jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def _verify_passkey_state(state: str, *, expected_purpose: str) -> dict[str, Any]:
    """Decode a ceremony state JWT, enforcing its purpose claim.

    Args:
        state: The token presented by the client on the complete step.
        expected_purpose: Which ceremony the caller expects the state
            to belong to.

    Returns:
        The decoded claims dictionary (challenge + user context).

    Raises:
        AppError: ``PASSKEY_REGISTRATION_FAILED`` on register-purpose
            states, ``PASSKEY_AUTH_FAILED`` on auth-purpose states,
            when the token is expired, tampered, or was minted for a
            different ceremony.
    """
    import jwt as _jwt

    fail_code = (
        PASSKEY_REGISTRATION_FAILED
        if expected_purpose == "passkey_register_challenge"
        else PASSKEY_AUTH_FAILED
    )
    settings = get_settings()
    try:
        claims = _jwt.decode(state, settings.jwt_secret_key, algorithms=["HS256"])
    except _jwt.PyJWTError as exc:
        raise AppError(
            code=fail_code,
            message="Passkey challenge state is invalid or expired.",
            status_code=400,
        ) from exc
    if claims.get("purpose") != expected_purpose:
        raise AppError(
            code=fail_code,
            message="Passkey challenge state has the wrong purpose.",
            status_code=400,
        )
    return claims  # type: ignore[no-any-return]


def _b64url_encode(raw: bytes) -> str:
    """Encode bytes as unpadded base64url.

    Args:
        raw: The bytes to encode.

    Returns:
        ASCII string with ``=`` padding stripped.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Decode an unpadded base64url string back to bytes.

    Args:
        value: ASCII base64url text, padding optional.

    Returns:
        The decoded bytes.
    """
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _transports_from_credential(credential: dict[str, Any]) -> str | None:
    """Extract the transports hint the browser reported, if any.

    Args:
        credential: The ``PublicKeyCredential`` JSON from the browser.

    Returns:
        A comma-separated list (e.g. ``"internal,hybrid"``) or None
        when the browser didn't report transports.
    """
    response = credential.get("response") if isinstance(credential, dict) else None
    if not isinstance(response, dict):
        return None
    transports = response.get("transports")
    if isinstance(transports, list) and transports:
        return ",".join(str(t) for t in transports)
    return None


def _render_email_html(*, verify_url: str) -> str:
    """Build the HTML body for the magic-link email.

    Kept intentionally simple — one paragraph, one prominent link. The
    plain-text fallback in :mod:`backend.services.email` strips the
    tags and still leaves a clickable URL.

    Args:
        verify_url: Absolute URL containing the raw token as a query
            parameter.

    Returns:
        HTML string ready to hand to SendGrid.
    """
    return (
        "<p>Tap the link below to finish signing in to Greenroom. "
        "This link is single-use and expires shortly.</p>"
        f'<p><a href="{verify_url}">Sign in to Greenroom</a></p>'
        f"<p>If the link doesn't work, paste this URL into your browser: "
        f"{verify_url}</p>"
    )

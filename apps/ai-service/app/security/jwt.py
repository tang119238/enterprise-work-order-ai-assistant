from __future__ import annotations

import base64
import json
import math
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import Settings

_MAX_TOKEN_LENGTH = 16_384
_CLOCK_SKEW_SECONDS = 60


class JwtAuthenticationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("JWT authentication failed")


class JwtTenantAuthenticator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key_path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not issuer.strip() or issuer.strip() != issuer:
            raise ValueError("JWT authentication configuration is invalid")
        if not audience.strip() or audience.strip() != audience:
            raise ValueError("JWT authentication configuration is invalid")
        try:
            key = serialization.load_pem_public_key(public_key_path.read_bytes())
        except (OSError, ValueError, TypeError):
            raise ValueError("JWT authentication configuration is invalid") from None
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError("JWT authentication configuration is invalid")
        self._issuer = issuer
        self._audience = audience
        self._public_key = key
        self._clock = clock

    def authenticate(self, authorization: str | None) -> UUID:
        try:
            token = self._bearer_token(authorization)
            encoded_header, encoded_claims, encoded_signature = token.split(".")
            header = self._decode_object(encoded_header)
            claims = self._decode_object(encoded_claims)
            if header.get("alg") != "RS256" or header.get("typ", "JWT") != "JWT":
                raise JwtAuthenticationError
            self._public_key.verify(
                self._decode_segment(encoded_signature),
                f"{encoded_header}.{encoded_claims}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            self._validate_claims(claims)
            return UUID(self._required_text(claims, "tenant_id"))
        except JwtAuthenticationError:
            raise
        except (
            InvalidSignature,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
        ):
            raise JwtAuthenticationError from None

    def _validate_claims(self, claims: Mapping[str, Any]) -> None:
        if self._required_text(claims, "iss") != self._issuer:
            raise JwtAuthenticationError
        self._required_text(claims, "sub")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = {audience}
        elif isinstance(audience, list) and all(
            isinstance(item, str) and item for item in audience
        ):
            audiences = set(audience)
        else:
            raise JwtAuthenticationError
        if self._audience not in audiences:
            raise JwtAuthenticationError
        now = self._clock()
        if not isinstance(now, int | float) or isinstance(now, bool) or not math.isfinite(now):
            raise JwtAuthenticationError
        not_before = self._integer_date(claims, "nbf")
        expires = self._integer_date(claims, "exp")
        if not_before > now + _CLOCK_SKEW_SECONDS:
            raise JwtAuthenticationError
        if expires <= now - _CLOCK_SKEW_SECONDS or expires <= not_before:
            raise JwtAuthenticationError

    @staticmethod
    def _bearer_token(authorization: str | None) -> str:
        if authorization is None or len(authorization) > _MAX_TOKEN_LENGTH + 7:
            raise JwtAuthenticationError
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or separator != " " or not token or " " in token:
            raise JwtAuthenticationError
        if len(token) > _MAX_TOKEN_LENGTH or token.count(".") != 2:
            raise JwtAuthenticationError
        return token

    @classmethod
    def _decode_object(cls, segment: str) -> Mapping[str, Any]:
        value = json.loads(cls._decode_segment(segment).decode("utf-8"))
        if not isinstance(value, dict):
            raise JwtAuthenticationError
        return value

    @staticmethod
    def _decode_segment(segment: str) -> bytes:
        if not segment:
            raise JwtAuthenticationError
        try:
            encoded = segment.encode("ascii")
            return base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, UnicodeEncodeError):
            raise JwtAuthenticationError from None

    @staticmethod
    def _required_text(claims: Mapping[str, Any], name: str) -> str:
        value = claims.get(name)
        if not isinstance(value, str) or not value.strip():
            raise JwtAuthenticationError
        return value

    @staticmethod
    def _integer_date(claims: Mapping[str, Any], name: str) -> int:
        value = claims.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise JwtAuthenticationError
        return value


def build_tenant_authenticator(settings: Settings) -> JwtTenantAuthenticator | None:
    values = (
        settings.jwt_issuer.strip(),
        settings.jwt_audience.strip(),
        settings.jwt_public_key_path,
    )
    if values == ("", "", None):
        return None
    if not values[0] or not values[1] or values[2] is None:
        raise ValueError("JWT authentication configuration is invalid")
    return JwtTenantAuthenticator(
        issuer=values[0],
        audience=values[1],
        public_key_path=values[2],
    )

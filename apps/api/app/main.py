"""Factory API entrypoint (first live FastAPI app in this repository).

FTF-0101 (OIDC login) is the first task that needs an actual running HTTP
service -- every prior CERTIFIED task deliberately disclosed "no live
FastAPI app exists yet" as the reason its own module had no live caller
(FTF-0005, FTF-0006, FTF-0102, FTF-0104). This module is that caller: it
builds and serves the real `/auth/*` router from
`app.auth.oidc_login.build_oidc_login_router`.

Secret resolution gap (disclosed, not hidden -- same class of gap FTF-0005
and FTF-R017 already carry): `OidcSettings` only carries *references*
(`vault://...`) for the OIDC client secret and the session-signing key.
No secret-manager integration exists anywhere in this repository yet, so
this module resolves them from two directly-injected environment
variables (`SOVRA_OIDC_CLIENT_SECRET_VALUE`, `SOVRA_SESSION_SIGNING_KEY_VALUE`)
rather than from the `*_secret_ref` values themselves. This is a real,
tracked gap for whichever task first wires an actual secret-manager
resolver -- not a production mock of the OIDC/session logic itself, which
is fully real from this point on.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from app.auth.oidc_login import build_oidc_login_router
from app.auth.oidc_settings import OidcSettings


def create_app() -> FastAPI:
    production = os.environ.get("SOVRA_ENVIRONMENT") == "production"
    settings = OidcSettings.from_env(production=production)

    # See module docstring: resolving `*_secret_ref` to a real value is a
    # disclosed, tracked gap -- no secret-manager integration exists yet.
    client_secret = os.environ["SOVRA_OIDC_CLIENT_SECRET_VALUE"]
    signing_key = os.environ["SOVRA_SESSION_SIGNING_KEY_VALUE"].encode("utf-8")

    app = FastAPI(title="Sovra Factory API")
    app.include_router(
        build_oidc_login_router(settings, client_secret=client_secret, signing_key=signing_key)
    )
    return app


# Run with `uvicorn app.main:create_app --factory` so settings are only
# resolved (and can only fail fast) at actual process startup, never as a
# side effect of importing this module from a test or type-checker.

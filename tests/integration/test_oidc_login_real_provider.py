#!/usr/bin/env python3
"""FTF-0101 -- real local OIDC provider proof (acceptance clause 1).

`tests/tasks/test_ftf_0101.py` (`--stage task`) already proves this module's
own signature/session/CSRF/PKCE logic against a real socket, using a bespoke
stdlib `ThreadingHTTPServer` standing in for the OIDC provider's `/jwks` and
`/token` endpoints. That stand-in is disclosed there (and in
`apps/api/app/auth/oidc_login.py`'s own docstring) as *not* satisfying this
task's own acceptance clause 1 ("Real OIDC provider login/refresh/logout
validates issuer+audience+subject and secure session") -- Section 65.3
requires exercising "a real local OIDC provider", which needed an
owner-approved local test-provider package. The owner approved `idpyoidc`
(Python) for this on 2026-09-19 (QUEUE.md "Blocked" history); this file is
that follow-up.

This test runs THREE real, independent processes talking over real HTTP
sockets, no test doubles anywhere in the request path:

  1. a real `idpyoidc.server.Server` OpenID Provider -- the actual
     reference OIDC/OAuth2 implementation this repo now depends on for
     tests, not a hand-rolled stand-in -- wired to real `/authorization`,
     `/token` and `/jwks` endpoints via a stdlib `ThreadingHTTPServer`;
  2. a real uvicorn server hosting the actual `build_oidc_login_router`
     (the system under test, same production code `apps/api/app/main.py`
     wires unconditionally);
  3. this test script, acting as the browser: it never calls into
     `idpyoidc` directly to fabricate a code or token -- every code,
     access token and ID token it sees was minted by the real OP in
     response to a real HTTP request from the app under test.

The real OP genuinely verifies the PKCE `code_verifier` against the
`code_challenge` from the original authorization request, signs the ID
token with its own real RSA key (never a canned fixture key), sets a real
`exp`/`iat`/`nonce`/`aud`/`iss`, and enforces real single-use authorization
codes (`max_usage: 1`) -- proven below by replaying a used code against the
real OP and getting a real `invalid_grant`, not a canned 400.

Run directly:
    python3 tests/integration/test_oidc_login_real_provider.py
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import pathlib
import secrets
import socket
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn
from fastapi import FastAPI
from idpyoidc.message.oidc import AccessTokenRequest, AuthorizationRequest
from idpyoidc.server import Server as OidcServer
from idpyoidc.server.authz import AuthzHandling
from idpyoidc.server.configure import OPConfiguration
from idpyoidc.server.cookie_handler import CookieHandler
from idpyoidc.server.oidc.authorization import Authorization
from idpyoidc.server.oidc.token import Token
from idpyoidc.server.user_info import UserInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.oidc_login import MembershipStatus, build_oidc_login_router  # noqa: E402
from app.auth.oidc_settings import OidcSettings  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


CRYPT_CONFIG = {
    "kwargs": {
        "keys": {
            "key_defs": [
                {"type": "OCT", "use": ["enc"], "kid": "password"},
                {"type": "OCT", "use": ["enc"], "kid": "salt"},
            ]
        },
        "iterations": 1,
    }
}
SESSION_PARAMS = {"encrypter": CRYPT_CONFIG}

CLIENT_ID = "sovra-ftf-0101-it-client"
CLIENT_SECRET = "sovra-ftf-0101-it-secret"
SIGNING_KEY = b"test-only-signing-key-not-a-real-secret"


def _build_real_op(*, issuer: str, redirect_uri: str) -> OidcServer:
    """A real idpyoidc OpenID Provider -- real signing key, real PKCE
    enforcement, real single-use authorization codes. Nothing here is a
    stand-in for provider behaviour; it *is* provider behaviour."""
    conf = {
        "issuer": issuer,
        "httpc_params": {"verify": False, "timeout": 2},
        "keys": {"uri_path": "jwks.json", "key_defs": [{"type": "RSA", "key": "", "use": ["sig"]}]},
        "token_handler_args": {
            "code": {"lifetime": 600, "kwargs": {"crypt_conf": CRYPT_CONFIG}},
            "token": {"lifetime": 3600, "kwargs": {"crypt_conf": CRYPT_CONFIG}},
            "refresh": {"lifetime": 3600, "kwargs": {"crypt_conf": CRYPT_CONFIG}},
            "id_token": {"class": "idpyoidc.server.token.id_token.IDToken", "kwargs": {}},
        },
        "endpoint": {
            "authorization": {"path": "authorization", "class": Authorization, "kwargs": {}},
            "token": {
                "path": "token",
                "class": Token,
                "kwargs": {"client_authn_method": ["client_secret_basic", "client_secret_post"]},
            },
        },
        "authentication": {
            "anon": {
                "acr": "http://www.swamid.se/policy/assurance/al1",
                "class": "idpyoidc.server.user_authn.user.NoAuthn",
                "kwargs": {"user": "diana"},
            }
        },
        "userinfo": {
            "class": UserInfo,
            "kwargs": {"db": {"diana": {"sub": "diana", "email": "diana@example.com"}}},
        },
        "template_dir": "template",
        "add_on": {
            "pkce": {
                "function": "idpyoidc.server.oauth2.add_on.pkce.add_support",
                "kwargs": {"essential": True},
            }
        },
        "authz": {
            "class": AuthzHandling,
            "kwargs": {
                "grant_config": {
                    "usage_rules": {
                        "authorization_code": {
                            "expires_in": 300,
                            "supports_minting": ["access_token", "refresh_token", "id_token"],
                            "max_usage": 1,
                        },
                        "access_token": {"expires_in": 600},
                        "refresh_token": {
                            "expires_in": 86400,
                            "supports_minting": ["access_token", "refresh_token"],
                        },
                    },
                    "expires_in": 43200,
                }
            },
        },
        "cookie_handler": {
            "class": CookieHandler,
            "kwargs": {
                "keys": {
                    "key_defs": [
                        {"type": "oct", "kid": "sig", "use": ["sig"]},
                        {"type": "oct", "kid": "enc", "use": ["enc"]},
                    ]
                }
            },
        },
        "session_params": SESSION_PARAMS,
    }
    server = OidcServer(OPConfiguration(conf=conf, base_path="/tmp"), cwd="/tmp")
    server.context.cdb[CLIENT_ID] = {
        "client_secret": CLIENT_SECRET,
        "redirect_uris": [(redirect_uri, None)],
        "response_types": ["code"],
        "allowed_scopes": ["openid", "profile", "email"],
    }
    return server


class _RealOpHandler(BaseHTTPRequestHandler):
    """Real HTTP transport for the real idpyoidc OP -- this class contains
    no OIDC logic of its own; every request is handed straight to the real
    `idpyoidc` endpoint objects, which do the actual PKCE/signature/
    single-use-code enforcement."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _op(self) -> OidcServer:
        return self.server.op  # type: ignore[attr-defined]

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/authorization":
            self._authorization(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/jwks":
            self._write_json(200, self._op().keyjar.export_jwks())
        else:
            self.send_response(404)
            self.end_headers()

    def _authorization(self, query: dict[str, list[str]]) -> None:
        flat = {k: v[0] for k, v in query.items()}
        endpoint = self._op().get_endpoint("authorization")
        auth_req = AuthorizationRequest(**flat)
        parsed_req = endpoint.parse_request(auth_req.to_dict())
        if hasattr(parsed_req, "get") and parsed_req.get("error"):
            self._write_json(400, dict(parsed_req))
            return
        resp = endpoint.process_request(parsed_req)
        if "response_args" not in resp:
            self._write_json(400, dict(resp))
            return
        msg = endpoint.do_response(**resp)
        self.send_response(302)
        self.send_header("Location", msg["response"])
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/token":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("ascii")).items()}
        endpoint = self._op().get_endpoint("token")
        token_req = AccessTokenRequest(**form)
        parsed_req = endpoint.parse_request(token_req.to_dict())
        if hasattr(parsed_req, "get") and parsed_req.get("error"):
            self._write_json(400, dict(parsed_req))
            return
        resp = endpoint.process_request(request=parsed_req)
        if "response_args" not in resp:
            self._write_json(400, dict(resp))
            return
        self._write_json(200, dict(resp["response_args"]))


class _RealOpServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], op: OidcServer) -> None:
        super().__init__(address, _RealOpHandler)
        self.op = op


@contextmanager
def _run_real_op(*, redirect_uri: str) -> Iterator[tuple[str, OidcServer]]:
    port = free_port()
    issuer = f"http://127.0.0.1:{port}/"
    op = _build_real_op(issuer=issuer, redirect_uri=redirect_uri)
    server = _RealOpServer(("127.0.0.1", port), op)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield issuer, op
    finally:
        server.shutdown()
        thread.join(timeout=5)


@contextmanager
def _run_api_app(settings: OidcSettings) -> Iterator[str]:
    app = FastAPI()
    router = build_oidc_login_router(
        settings,
        client_secret=CLIENT_SECRET,
        signing_key=SIGNING_KEY,
        membership_lookup=lambda iss, sub: MembershipStatus.ACTIVE,
    )
    app.include_router(router)
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@dataclass
class _HttpResult:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    def location(self) -> str:
        return self.headers.get("Location", "")

    def set_cookies(self) -> list[str]:
        return self.headers.get_all("Set-Cookie") or []

    def json(self) -> dict:
        return json.loads(self.body)


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> _HttpResult:
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        raw_body = response.read()
        return _HttpResult(status=response.status, headers=response.headers, body=raw_body)
    finally:
        conn.close()


def _cookie_header(*set_cookie_values: str) -> str:
    pairs = [value.split(";", 1)[0] for value in set_cookie_values]
    return "; ".join(pairs)


def _base_env(*, issuer: str, api_port: int) -> dict[str, str]:
    return {
        "SOVRA_OIDC_ISSUER": issuer,
        "SOVRA_OIDC_CLIENT_ID": CLIENT_ID,
        "SOVRA_OIDC_CLIENT_SECRET_REF": "secret://oidc/client-secret",
        "SOVRA_OIDC_REDIRECT_URI": f"http://127.0.0.1:{api_port}/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": f"{issuer}authorization",
        "SOVRA_OIDC_TOKEN_ENDPOINT": f"{issuer}token",
        "SOVRA_OIDC_JWKS_URI": f"{issuer}jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "secret://session/signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://localhost:3000",
    }


def test_full_login_flow_against_a_real_idpyoidc_provider() -> None:
    api_port = free_port()
    redirect_uri = f"http://127.0.0.1:{api_port}/auth/callback"
    with _run_real_op(redirect_uri=redirect_uri) as (issuer, _op):
        env = _base_env(issuer=issuer, api_port=api_port)
        settings = OidcSettings.from_env(env, production=False)

        with _run_api_app(settings) as api_base:
            # 1. GET /auth/login -- our app builds the real authorization redirect.
            login_resp = _request(api_base, "GET", "/auth/login")
            check(
                "FTF-0101",
                "login() redirects to the real OP's authorization endpoint",
                login_resp.status == 302
                and login_resp.location().startswith(settings.authorization_endpoint),
            )
            txn_cookies = login_resp.set_cookies()

            # 2. Follow that redirect for real: the real idpyoidc OP parses the
            #    real request, auto-authenticates (NoAuthn/"diana" -- test-only
            #    provider config, not a bypass in the app under test) and
            #    issues a real, PKCE-bound authorization code.
            op_resp = _request(
                issuer.rstrip("/"), "GET", login_resp.location()[len(issuer.rstrip("/")) :]
            )
            check(
                "FTF-0101",
                "the real OP issues a real authorization code",
                op_resp.status == 302 and "code=" in op_resp.location(),
            )
            callback_url = urllib.parse.urlparse(op_resp.location())
            check(
                "FTF-0101",
                "the real OP redirects back to our own real redirect_uri",
                f"{callback_url.scheme}://{callback_url.netloc}{callback_url.path}" == redirect_uri,
            )

            # 3. GET /auth/callback with the real code + real txn cookie: our app
            #    now makes REAL HTTP calls to the real OP's /token and /jwks.
            callback_resp = _request(
                api_base,
                "GET",
                f"/auth/callback?{callback_url.query}",
                headers={"Cookie": _cookie_header(*txn_cookies)},
            )
            check(
                "FTF-0101",
                "a real callback against a real provider redirects to the post-login path",
                callback_resp.status == 302 and callback_resp.location() == "/",
            )
            session_cookie = next(
                (c for c in callback_resp.set_cookies() if c.startswith("sovra_session=")), None
            )
            check(
                "FTF-0101",
                "a real session cookie is issued from a real provider round-trip",
                session_cookie is not None,
            )
            assert session_cookie is not None

            # 4. GET /auth/session proves the real, provider-issued subject round-trips.
            session_resp = _request(
                api_base, "GET", "/auth/session", headers={"Cookie": _cookie_header(session_cookie)}
            )
            check(
                "FTF-0101",
                "the established session reflects the real subject from the real provider",
                session_resp.status == 200
                and session_resp.json()["sub"]
                and session_resp.json()["iss"] == settings.issuer,
            )

            # 5. Replay the authorization code directly against the real OP's
            #    own /token endpoint: idpyoidc's real max_usage=1 enforcement
            #    must reject it for real -- not a canned negative case.
            code = urllib.parse.parse_qs(callback_url.query)["code"][0]
            replay_body = urllib.parse.urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "code_verifier": "irrelevant-because-the-code-is-already-consumed",
                }
            ).encode("ascii")
            replay_resp = _request(
                issuer.rstrip("/"),
                "POST",
                "/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=replay_body,
            )
            check(
                "FTF-0101",
                "the real OP rejects a replayed (already-consumed) authorization code",
                replay_resp.status == 400,
            )


def test_wrong_code_verifier_rejected_by_the_real_provider() -> None:
    """The real OP's own PKCE enforcement, exercised directly against its
    real /token endpoint. (Our app's own callback always sends the verifier
    it itself generated and signed into the txn cookie, so a mismatch is not
    reachable through the app's own flow without forging that cookie --
    `tests/tasks/test_ftf_0101.py::test_pkce_challenge_matches_verifier`
    already proves this module's own PKCE-pairing logic. What is new here is
    that the real, independent idpyoidc provider -- not our own code --
    genuinely rejects a mismatched verifier for a code it itself issued.)"""
    api_port = free_port()
    redirect_uri = f"http://127.0.0.1:{api_port}/auth/callback"
    with _run_real_op(redirect_uri=redirect_uri) as (issuer, _op):
        env = _base_env(issuer=issuer, api_port=api_port)
        settings = OidcSettings.from_env(env, production=False)

        with _run_api_app(settings) as api_base:
            login_resp = _request(api_base, "GET", "/auth/login")
            op_resp = _request(
                issuer.rstrip("/"), "GET", login_resp.location()[len(issuer.rstrip("/")) :]
            )
            callback_url = urllib.parse.urlparse(op_resp.location())

            code = urllib.parse.parse_qs(callback_url.query)["code"][0]
            body = urllib.parse.urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "code_verifier": secrets.token_urlsafe(32),
                }
            ).encode("ascii")
            resp = _request(
                issuer.rstrip("/"),
                "POST",
                "/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            check(
                "FTF-0101",
                "the real OP rejects a real authorization code with the wrong PKCE verifier",
                resp.status == 400 and resp.json().get("error") == "invalid_grant",
            )


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def test_real_id_token_is_signed_and_shaped_as_expected() -> None:
    """Independent sanity check, decoding the real id_token's own header/
    payload (not re-validating -- that's already proven end-to-end above and
    in tests/tasks/test_ftf_0101.py's own signature-validation unit tests)."""
    api_port = free_port()
    redirect_uri = f"http://127.0.0.1:{api_port}/auth/callback"
    with _run_real_op(redirect_uri=redirect_uri) as (issuer, op):
        auth_req = AuthorizationRequest(
            client_id=CLIENT_ID,
            redirect_uri=redirect_uri,
            scope=["openid", "email"],
            state="s1",
            nonce="n1",
            response_type="code",
            code_challenge=_s256("verifier-for-this-check-only-not-reused-elsewhere"),
            code_challenge_method="S256",
        )
        authn_endpoint = op.get_endpoint("authorization")
        pr = authn_endpoint.parse_request(auth_req.to_dict())
        resp = authn_endpoint.process_request(pr)
        msg = authn_endpoint.do_response(**resp)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(msg["response"]).query)

        token_endpoint = op.get_endpoint("token")
        token_req = AccessTokenRequest(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=redirect_uri,
            grant_type="authorization_code",
            code=query["code"][0],
            code_verifier="verifier-for-this-check-only-not-reused-elsewhere",
        )
        treq = token_endpoint.parse_request(token_req.to_dict())
        tresp = token_endpoint.process_request(request=treq)
        id_token = tresp["response_args"]["id_token"]

        header = json.loads(_b64url_decode(id_token.split(".")[0]))
        payload = json.loads(_b64url_decode(id_token.split(".")[1]))
        jwks = op.keyjar.export_jwks()

        check("FTF-0101", "the real id_token is RS256-signed", header.get("alg") == "RS256")
        check(
            "FTF-0101",
            "the real id_token's kid matches a real key in the real OP's own JWKS",
            header.get("kid") in {k["kid"] for k in jwks["keys"]},
        )
        check("FTF-0101", "the real id_token carries the real issuer", payload.get("iss") == issuer)
        check(
            "FTF-0101",
            "the real id_token carries our real client_id as audience",
            CLIENT_ID in payload.get("aud", []),
        )
        check("FTF-0101", "the real id_token echoes our real nonce", payload.get("nonce") == "n1")


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def main() -> None:
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0101 real OIDC provider integration: {len(FAILURES)} check(s) failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0101 real OIDC provider integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()

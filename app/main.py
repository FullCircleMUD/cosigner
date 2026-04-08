"""
FastAPI application for the XRPL co-signing service.

Endpoints:
    POST /cosign  — Co-sign and submit an XRPL transaction
    GET  /health  — Health check
"""

import logging
import os
import sys

from fastapi import Depends, FastAPI, Header, HTTPException

from app.config import AppConfig, load_config
from app.models import CosignRequest, CosignResponse, ErrorResponse
from app.signer import CosignError, cosign_and_submit

# ── App setup ────────────────────────────────────────────────────────

app = FastAPI(
    title="XRPL Co-Signing Service",
    description="Multi-wallet XRPL transaction co-signer with per-wallet business rules.",
    version="0.1.0",
)

_config: AppConfig | None = None


def _get_config() -> AppConfig:
    """Lazy-load configuration on first request."""
    global _config
    if _config is None:
        _config = load_config()
        logging.basicConfig(
            level=getattr(logging, _config.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    return _config


def _verify_api_key(
    authorization: str = Header(alias="X-API-Key"),
    config: AppConfig = Depends(_get_config),
) -> AppConfig:
    """Verify the API key from the request header."""
    if authorization != config.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return config


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — no authentication required."""
    config = _get_config()
    wallets = {w.name: w.network_url for w in config.wallets.values()}
    return {
        "status": "ok",
        "wallets": wallets,
    }


@app.post(
    "/cosign",
    response_model=CosignResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def cosign(
    request: CosignRequest,
    config: AppConfig = Depends(_verify_api_key),
):
    """
    Co-sign a partially-signed XRPL transaction and submit to the network.

    The transaction blob must already contain one signature (key A, signed
    with multisign=True). This endpoint adds the co-signer's signature
    (key B), combines them, and submits to XRPL.

    Requires X-API-Key header for authentication.
    """
    try:
        result = await cosign_and_submit(request.tx_blob, config)
    except CosignError as e:
        status_map = {
            "invalid_transaction": 400,
            "unknown_wallet": 400,
            "missing_signature": 400,
            "rule_violation": 403,
            "signing_failed": 500,
            "combine_failed": 500,
            "submission_failed": 502,
            "xrpl_error": 502,
        }
        status = status_map.get(e.error_type, 500)
        raise HTTPException(
            status_code=status,
            detail={"error": e.error_type, "detail": e.detail},
        )

    return CosignResponse(**result)


# ── Startup ──────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Validate configuration on startup."""
    try:
        config = _get_config()
    except ValueError as e:
        logging.error("Configuration error: %s", e)
        sys.exit(1)
    logging.info(
        "Co-signing service started — %d wallet(s) configured",
        len(config.wallets),
    )

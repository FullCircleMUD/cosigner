"""
Pydantic request/response schemas for the co-signing API.
"""

from pydantic import BaseModel, Field


class CosignRequest(BaseModel):
    """Request to co-sign an XRPL transaction."""

    tx_blob: str = Field(
        description=(
            "Serialised XRPL transaction blob (hex string), "
            "already partially signed with key A (multisign=True)."
        ),
    )


class CosignResponse(BaseModel):
    """Response from a successful co-sign + submission."""

    tx_hash: str = Field(description="XRPL transaction hash")
    engine_result: str = Field(
        description="XRPL engine result code (e.g. 'tesSUCCESS')"
    )
    wallet_name: str = Field(
        description="Name of the wallet that was co-signed for"
    )
    meta: dict = Field(
        default_factory=dict,
        description="Transaction metadata (AffectedNodes, etc.) from XRPL submission",
    )


class ErrorResponse(BaseModel):
    """Error response."""

    error: str = Field(description="Error type")
    detail: str = Field(description="Human-readable error message")

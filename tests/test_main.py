"""
Unit tests for FastAPI endpoints.

Tests app/main.py — health check, API key auth, error code mapping.
Uses FastAPI TestClient with mocked config and cosign_and_submit.
"""

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import AppConfig, WalletConfig, WalletRules
import app.main as main_module
from app.main import app, _get_config
from app.signer import CosignError


VAULT_ADDRESS = "rVAULT111111111111111111111111111"
TEST_API_KEY = "test-secret-key"


def _test_config():
    """Create a test AppConfig."""
    return AppConfig(
        api_key=TEST_API_KEY,
        log_level="INFO",
        wallets={
            VAULT_ADDRESS: WalletConfig(
                address=VAULT_ADDRESS,
                name="vault",
                seed="sEdFAKESEED",
                network_url="wss://s.altnet.rippletest.net:51233",
                rules=WalletRules(
                    allowed_tx_types=frozenset(["Payment"]),
                    blocked_tx_types=frozenset(["AccountDelete"]),
                    max_per_minute=30,
                ),
            )
        },
    )


@pytest.fixture
def client():
    """FastAPI test client with pre-loaded config to bypass startup's load_config()."""
    config = _test_config()
    # Pre-set the module-level _config so startup() doesn't call load_config()
    main_module._config = config
    app.dependency_overrides[_get_config] = lambda: config
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    main_module._config = None


class TestHealthEndpoint:
    def test_health_endpoint(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "vault" in data["wallets"]
        assert "altnet" in data["wallets"]["vault"]


class TestCosignAuth:
    def test_cosign_missing_api_key(self, client):
        response = client.post("/cosign", json={"tx_blob": "deadbeef"})
        assert response.status_code == 422  # missing required header

    def test_cosign_bad_api_key(self, client):
        response = client.post(
            "/cosign",
            json={"tx_blob": "deadbeef"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401


class TestCosignEndpoint:
    @patch("app.main.cosign_and_submit", new_callable=AsyncMock)
    def test_cosign_success(self, mock_cosign, client):
        mock_cosign.return_value = {
            "tx_hash": "AABB1234",
            "engine_result": "tesSUCCESS",
            "wallet_name": "vault",
            "meta": {"TransactionResult": "tesSUCCESS"},
        }

        response = client.post(
            "/cosign",
            json={"tx_blob": "deadbeef"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tx_hash"] == "AABB1234"
        assert data["engine_result"] == "tesSUCCESS"
        assert data["wallet_name"] == "vault"

    @patch("app.main.cosign_and_submit", new_callable=AsyncMock)
    def test_cosign_invalid_tx_400(self, mock_cosign, client):
        mock_cosign.side_effect = CosignError("invalid_transaction", "bad blob")

        response = client.post(
            "/cosign",
            json={"tx_blob": "bad"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 400
        data = response.json()["detail"]
        assert data["error"] == "invalid_transaction"

    @patch("app.main.cosign_and_submit", new_callable=AsyncMock)
    def test_cosign_rule_violation_403(self, mock_cosign, client):
        mock_cosign.side_effect = CosignError("rule_violation", "type blocked")

        response = client.post(
            "/cosign",
            json={"tx_blob": "deadbeef"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 403
        data = response.json()["detail"]
        assert data["error"] == "rule_violation"

    @patch("app.main.cosign_and_submit", new_callable=AsyncMock)
    def test_cosign_xrpl_error_502(self, mock_cosign, client):
        mock_cosign.side_effect = CosignError("xrpl_error", "tecPATH_DRY")

        response = client.post(
            "/cosign",
            json={"tx_blob": "deadbeef"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 502
        data = response.json()["detail"]
        assert data["error"] == "xrpl_error"

    @patch("app.main.cosign_and_submit", new_callable=AsyncMock)
    def test_cosign_signing_failed_500(self, mock_cosign, client):
        mock_cosign.side_effect = CosignError("signing_failed", "key error")

        response = client.post(
            "/cosign",
            json={"tx_blob": "deadbeef"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 500
        data = response.json()["detail"]
        assert data["error"] == "signing_failed"

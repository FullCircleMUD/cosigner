# XRPL Co-Signing Service

Multi-wallet XRPL transaction co-signer with per-wallet business rules. Provides the second signature for XRPL native multisig wallets.

## Architecture

```
Game Server (Railway)                Co-Signing Service (Render)
┌──────────────────┐                ┌──────────────────────────┐
│ Build tx         │                │                          │
│ Autofill         │  POST /cosign  │ 1. Authenticate (API key)│
│ Sign with key A  │───────────────→│ 2. Look up wallet config │
│ (multisign=True) │                │ 3. Validate rules        │
│                  │  {tx_hash,     │ 4. Co-sign with key B    │
│                  │   result}      │ 5. Combine signatures    │
│                  │←───────────────│ 6. Submit to XRPL        │
└──────────────────┘                └──────────────────────────┘
```

The game server and co-signer run on **different infrastructure providers**. Compromising one system doesn't give access to both signing keys.

## Quick Start

```bash
# 1. Install dependencies
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp wallets.json.example wallets.json   # edit wallet addresses + rules
cp .env.example .env                    # edit seeds + API key

# 3. Run
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Configuration

### wallets.json

Maps XRPL account addresses to signing keys and business rules. Each wallet has:
- `name` — human-readable label
- `seed_env` — name of the env var holding the signing seed (secret stays in env, not in JSON)
- `network_url` — XRPL websocket endpoint for this wallet (required)
- `rules` — per-wallet validation rules

```json
{
  "wallets": {
    "rVaultAddress...": {
      "name": "vault",
      "seed_env": "SIGNER_B_SEED_VAULT",
      "network_url": "wss://xrplcluster.com",
      "rules": {
        "allowed_tx_types": ["Payment", "NFTokenCreateOffer", "NFTokenAcceptOffer", "OfferCreate"],
        "blocked_tx_types": ["AccountDelete", "SignerListSet", "SetRegularKey", "AccountSet"],
        "require_issuer": "rIssuerAddress...",
        "max_per_minute": 30
      }
    }
  }
}
```

Adding a new wallet = add a JSON entry + set an env var. No code changes, no redeploy.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | Yes | Production API key — requests using this key co-sign and submit to XRPL |
| `DEV_API_KEY` | No | Dev API key — requests using this key run the full pipeline but skip XRPL submission, returning a mock success response. Safe to share with dev environments |
| `WALLETS_CONFIG` | No | Path to wallets.json (default: `./wallets.json`) |
| `<SEED_ENV>` | Per wallet | Signing seed — env var name matches `seed_env` in wallets.json (e.g. `SIGNER_B_SEED_VAULT`) |
| `LOG_LEVEL` | No | DEBUG, INFO, WARNING, ERROR (default: INFO) |

Network URLs are configured per wallet in `wallets.json`, not as global env vars. This allows a single instance to serve wallets on different XRPL networks.

### Business Rules

| Rule | Config Key | Description |
|------|-----------|-------------|
| Allowed types | `allowed_tx_types` | Transaction type must be in this list |
| Blocked types | `blocked_tx_types` | Transaction type must NOT be in this list |
| Currency issuer | `require_issuer` | All issued currency amounts must reference this issuer |
| Rate limit | `max_per_minute` | Max transactions per minute per wallet |

## API

### `POST /cosign`

Co-sign and submit a partially-signed XRPL transaction.

**Headers:** `X-API-Key: <api_key>`

**Request:**
```json
{
  "tx_blob": "<hex-encoded transaction blob, signed with key A>"
}
```

**Response (200):**
```json
{
  "tx_hash": "ABC123...",
  "engine_result": "tesSUCCESS",
  "wallet_name": "vault"
}
```

**Error responses:** 400 (bad request), 401 (invalid API key), 403 (rule violation), 502 (XRPL error)

### `GET /health`

Health check (no authentication).

```json
{
  "status": "ok",
  "wallets": {
    "vault": "wss://xrplcluster.com"
  }
}
```

## Setup Scripts

### Generate Keypairs

```bash
python setup/generate_keys.py --count 3 --labels vault_a,cosigner_b,recovery_c
```

### Configure SignerList

```bash
# Set up 2-of-2 on vault
python setup/configure_signerlist.py \
  --network wss://xrplcluster.com \
  --account-seed sEdMasterKey... \
  --signers rKeyA:1,rKeyB:1 \
  --quorum 2

# Verify
python setup/configure_signerlist.py \
  --network wss://xrplcluster.com \
  --account-seed sEdMasterKey... \
  --verify-only

# Remove (restore single-sig)
python setup/configure_signerlist.py \
  --network wss://xrplcluster.com \
  --account-seed sEdMasterKey... \
  --remove
```

## Tests

```bash
python -m pytest tests/ -v
```

51 tests covering config loading, co-sign logic, FastAPI endpoints, business rules, and dev mode.

## Deployment (Render)

1. Connect GitHub repo to Render
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Set environment variables: `API_KEY`, `DEV_API_KEY` (optional), seed env vars, `LOG_LEVEL`, `WALLETS_CONFIG`
5. Upload `wallets.json` via Render Secret Files (mount at `/etc/secrets/wallets.json`, set `WALLETS_CONFIG=/etc/secrets/wallets.json`)
6. Set health check path to `/health`

## Integration Test

```bash
python setup/integration_test.py \
    --api-key <COSIGNER_API_KEY> \
    --signer-seed <KEY_A_SEED> \
    --vault-address <VAULT_ADDRESS> \
    --issuer-address <ISSUER_ADDRESS>
```

Sends a tiny (0.001 FCMGold) payment from vault to issuer. Works with both production and dev API keys — dev mode runs the full pipeline but skips on-chain submission.

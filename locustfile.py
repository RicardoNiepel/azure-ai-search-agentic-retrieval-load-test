"""
Locust load test for Azure AI Search - Agentic Retrieval.

This script exercises the Knowledge Retrieval REST API against a specified
knowledge base.

Authentication strategy
-----------------------
* Local execution  – uses the Azure CLI credential (the user currently logged
  in via `az login`).  Requires the `azure-identity` package.
* Azure Load Testing – uses the Azure Instance Metadata Service (IMDS) to
  obtain a bearer token for the managed identity assigned to the load-test
  engine.  No extra packages are needed; the request is made over plain HTTP
  to the link-local IMDS endpoint.

Environment detection
---------------------
On startup the script probes the IMDS endpoint with a 1-second timeout.
If it responds, we are running inside Azure; otherwise we fall back to the
Azure CLI credential.  This means no manual flag needs to be set – it works
automatically for both local and cloud runs.

Required environment variables (configure in config.yaml)
----------------------------------------------------------
SEARCH_ENDPOINT       – Full URL of the Azure AI Search service, e.g.
                        https://my-service.search.windows.net
KNOWLEDGE_BASE_NAME   – Name of the target knowledge base for Agentic Retrieval.
"""

import json
import logging
import os
import time
from typing import Tuple, Optional

import requests as _requests  # used only for IMDS + environment detection
from dotenv import load_dotenv  # type: ignore[import]
from locust import HttpUser, between, events, task

# Load variables from a local .env file when present.
# Variables already set in the process environment (e.g. by Azure Load Testing)
# are NOT overwritten because load_dotenv() defaults to override=False.
load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (override via environment variables / config.yaml env section)
# ---------------------------------------------------------------------------
SEARCH_ENDPOINT = os.environ.get(
    "SEARCH_ENDPOINT", "https://<your-service>.search.windows.net"
)
KNOWLEDGE_BASE_NAME = os.environ.get("KNOWLEDGE_BASE_NAME", "your-knowledge-base")
API_VERSION = "2025-11-01-preview"

# The resource / scope used when requesting tokens for Azure AI Search.
# IMDS uses the resource URL; azure-identity uses the ".default" scope form.
AZURE_SEARCH_RESOURCE = "https://search.azure.com/"
AZURE_SEARCH_SCOPE = f"{AZURE_SEARCH_RESOURCE}.default"

# ---------------------------------------------------------------------------
# Environment detection – probe IMDS once at module load time
# ---------------------------------------------------------------------------
_IMDS_INSTANCE_URL = (
    "http://169.254.169.254/metadata/instance?api-version=2021-02-01"
)


def _detect_azure_environment() -> bool:
    """Return True when running inside an Azure compute environment (IMDS reachable)."""
    try:
        resp = _requests.get(
            _IMDS_INSTANCE_URL,
            headers={"Metadata": "true"},
            timeout=1,
        )
        return resp.status_code == 200
    except Exception:
        return False


IS_AZURE: bool = _detect_azure_environment()
log.info(
    "Running in %s – will use %s for authentication.",
    "Azure" if IS_AZURE else "local",
    "Managed Identity (IMDS)" if IS_AZURE else "Azure CLI credential",
)

# ---------------------------------------------------------------------------
# Token cache – shared across all Locust users in the same worker process
# ---------------------------------------------------------------------------
_token_cache: dict = {"access_token": None, "expires_on": 0.0}

# Refresh the token this many seconds before its actual expiry to avoid
# sending requests with an expired token.
_TOKEN_REFRESH_BUFFER_SECONDS = 60


def _fetch_token_via_imds() -> Tuple[str, float]:
    """
    Retrieve a bearer token from the Azure Instance Metadata Service (IMDS).

    Reference:
    https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-use-vm-token#get-a-token-using-http
    """
    url = (
        "http://169.254.169.254/metadata/identity/oauth2/token"
        f"?api-version=2018-02-01&resource={AZURE_SEARCH_RESOURCE}"
    )
    response = _requests.get(url, headers={"Metadata": "true"}, timeout=10)
    response.raise_for_status()
    data: dict = response.json()
    # `expires_on` is returned as a string containing a Unix timestamp.
    return data["access_token"], float(data["expires_on"])


def _fetch_token_via_azure_cli() -> Tuple[str, float]:
    """
    Retrieve a bearer token via the Azure CLI credential.

    The user running the test locally must be authenticated with `az login`.
    Requires the `azure-identity` package (see requirements.txt).
    """
    from azure.identity import AzureCliCredential  # type: ignore[import]

    credential = AzureCliCredential()
    token = credential.get_token(AZURE_SEARCH_SCOPE)
    # AccessToken.expires_on is an int (Unix timestamp, seconds since epoch).
    return token.token, float(token.expires_on)


def get_valid_token() -> str:
    """
    Return a valid bearer token, fetching a new one when the cached token is
    absent or about to expire.

    This function is thread-safe enough for the single-process Locust model.
    If you run Locust with multiple workers you may want to add a lock, but
    a few redundant refreshes are harmless given token caching on the IMDS side.
    """
    now = time.time()
    if (
        _token_cache["access_token"] is None
        or now >= _token_cache["expires_on"] - _TOKEN_REFRESH_BUFFER_SECONDS
    ):
        log.info("Acquiring a new bearer token (IS_AZURE=%s)...", IS_AZURE)
        if IS_AZURE:
            access_token, expires_on = _fetch_token_via_imds()
        else:
            access_token, expires_on = _fetch_token_via_azure_cli()

        _token_cache["access_token"] = access_token
        _token_cache["expires_on"] = expires_on
        log.info(
            "Token acquired, valid until %s UTC.",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_on)),
        )

    return _token_cache["access_token"]


# ---------------------------------------------------------------------------
# Locust user
# ---------------------------------------------------------------------------
class AgenticRetrievalUser(HttpUser):
    """
    Virtual user that repeatedly calls the Azure AI Search - Agentic Retrieval
    Knowledge Retrieval REST API on the configured knowledge base.

    POST {endpoint}/knowledgebases('{knowledgeBaseName}')/retrieve
         ?api-version=2025-11-01-preview
    """

    host = SEARCH_ENDPOINT
    wait_time = between(1, 3)

    # Default request payload.  Customise the query text and other parameters as needed.
    _PAYLOAD: dict = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "How should we handle credit card details from customers?",
                    }
                ],
            }
        ],
        "maxRuntimeInSeconds": 60,
        "maxOutputSize": 100000,
        "retrievalReasoningEffort": {"kind": "low"},
        "includeActivity": False,
        "outputMode": "answerSynthesis",
    }


    @task
    def call_knowledge_retrieval_rest_api(self) -> None:
        token = get_valid_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json;odata.metadata=minimal",
        }

        url = f"/knowledgebases('{KNOWLEDGE_BASE_NAME}')/retrieve"

        with self.client.post(
            url,
            json=self._PAYLOAD,
            headers=headers,
            params={"api-version": API_VERSION},
            name="Knowledge Retrieval REST API - POST /knowledgebases('{knowledgeBaseName}')/retrieve",
            catch_response=True,
        ) as response:
            # HTTP 200 = complete response; 206 = partial (still a success)
            if response.status_code in (200, 206):
                response.success()
            else:
                response.failure(
                    f"Unexpected status {response.status_code}: "
                    f"{response.text[:300]}"
                )



# Azure AI Search - Agentic Retrieval Load Test

A [Locust](https://locust.io/) load test for the
[Azure AI Search - Agentic Retrieval](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview), using the [Knowledge Retrieval REST API](https://learn.microsoft.com/en-us/rest/api/searchservice/knowledge-retrieval/retrieve?view=rest-searchservice-2025-11-01-preview),
ready to run both **locally** via the
[Azure Load Testing VS Code extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-azureloadtesting)
and **in the cloud** via the [Azure Load Testing service](https://learn.microsoft.com/en-us/azure/load-testing/overview).

---

## Repository layout

```
.
├── locustfile.py      # Locust test script
├── config.yaml        # Azure Load Testing configuration
├── requirements.txt   # Python dependencies
├── .env.example       # Template for local environment variables
└── .gitignore         # Excludes .env from version control
```

---

## How it works

Each virtual user repeatedly calls the Knowledge Retrieval REST API on the configured knowledge base:

```
POST {SEARCH_ENDPOINT}/knowledgebases('{KNOWLEDGE_BASE_NAME}')/retrieve
     ?api-version=2025-11-01-preview
```

with a Bearer token in the `Authorization` header.

### Authentication

| Execution context              | Credential used                                                                                                                                 |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Local** (your machine)       | `AzureCliCredential` from `azure-identity` – uses the account you signed in with via `az login`.                                                |
| **Azure Load Testing** (cloud) | Azure Instance Metadata Service (IMDS) at `http://169.254.169.254` – obtains a token for the managed identity assigned to the load-test engine. |

The script auto-detects the environment by probing the IMDS endpoint at startup
(1-second timeout). No manual flag is required.

Tokens are cached in-process and refreshed automatically 60 seconds before expiry.

---

## Prerequisites

| Tool                                                                                                                              | Purpose                                                                 |
| --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Python 3.9+                                                                                                                       | Run the test locally                                                    |
| Locust                                                                                                                            | Required to execute the test locally (installed via `requirements.txt`) |
| `pip install -r requirements.txt`                                                                                                 | Install dependencies, including Locust                                  |
| [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)                                                        | Authenticate locally (`az login`)                                       |
| [Azure Load Testing VS Code extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-azureloadtesting) | Run the test locally or in the cloud                                    |

---

## Local setup

### 1. Install dependencies

Locust must be installed to run this test locally. Installing from
`requirements.txt` handles this for you.

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables via `.env`

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env`:

```dotenv
SEARCH_ENDPOINT=https://mysearch.search.windows.net
KNOWLEDGE_BASE_NAME=my-knowledge-base
```

The `.env` file is loaded automatically by `locustfile.py` on startup.
Variables already set in the shell environment take precedence over `.env` values.

> **Security note:** `.env` is listed in `.gitignore` and must never be committed
> to source control.

### 3. Sign in with the Azure CLI

```bash
az login

# or to login into a specific tenant
az login --tenant <tenant-id>
```

Your account must have at least the **Search Index Data Reader** role on the
Azure AI Search resource (see [RBAC requirements](#rbac-requirements)).

---

## Running locally with the VS Code extension

1. Open the workspace in VS Code.
2. Open **`config.yaml`** in the editor.
3. Execute command **Load Testing: Run load test (local)**.
4. The extension starts Locust on your machine, reading configuration from
   `config.yaml` and environment variables from your `.env` file.

---

## Running in Azure Load Testing

1. Execute command `Load Testing: Run load test (Azure Load Testing)`, select `locustfile.py` and  then select or create an Azure Load Testing resource. Also select `Monitor server-side metrics` to add your Azure AI Search resource as a reference identity for monitoring during the test run.

2. adjust the `displayName` and `description` fields in `loadtest.config.yaml` if desired – these are shown in the Azure portal to identify your test configuration.

3. **WORKAROUND NEEDED** add the following to the `referenceIdentities` section of your `loadtest.config.yaml` to allow the load testing engine to authenticate to Azure AI Search using its managed identity:

```yaml
referenceIdentities:
- type: SystemAssigned
   kind: Engine
```

> **WORKAROUND:** Because of a bug this needs to be done manually in the portal after deployment: [Configure the Managed identity for authentication scenarios](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-test-secured-endpoints?tabs=portal#select-the-managed-identity-in-the-load-test-configuration)

4. The extension packages `locustfile.py`, `config.yaml`, and
   `requirements.txt`, uploads them, and starts the test run in the cloud.

When running in Azure, the script uses the **managed identity** of the
load-test engine to obtain a token via IMDS – no credentials are embedded in
the test plan.

---

## RBAC requirements

| Context            | Identity                                            | Required role              | Scope                    |
| ------------------ | --------------------------------------------------- | -------------------------- | ------------------------ |
| Local              | Your Azure AD user                                  | `Search Index Data Reader` | Azure AI Search resource |
| Azure Load Testing | Managed identity of the Azure Load Testing resource | `Search Index Data Reader` | Azure AI Search resource |
| Azure Load Testing | Managed identity of the Azure Load Testing resource | `Monitoring Reader`        | Resource group           |

Assign the role with the Azure CLI:

```bash
# Replace the placeholders with your actual values
az role assignment create \
  --role "Search Index Data Reader" \
  --assignee <object-id-or-user-email> \
  --scope /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Search/searchServices/<service-name>
```

---

## Customising the test

| What to change                                     | Where                                                                                   |
| -------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Query text                                         | `AgenticRetrievalUser._PAYLOAD["messages"][0]["content"][0]["text"]` in `locustfile.py` |
| Reasoning effort (`low` / `medium` / `minimal`)    | `AgenticRetrievalUser._PAYLOAD["retrievalReasoningEffort"]`                             |
| Output mode (`answerSynthesis` / `extractiveData`) | `AgenticRetrievalUser._PAYLOAD["outputMode"]`                                           |
| Virtual users & ramp-up                            | Locust web UI or `config.yaml` (`engineInstances`)                                      |
| Wait time between requests                         | `wait_time = between(1, 3)` in `AgenticRetrievalUser`                                   |
| Failure thresholds                                 | `failureCriteria` section of `config.yaml`                                              |

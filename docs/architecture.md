# Architecture

## System overview

```mermaid
flowchart LR
    subgraph Targets
        D[Demo shop<br/>flaky_server.py :8001]
        P[Public sites<br/>google / github-api / httpbin]
    end

    subgraph Monitoring
        S[scheduler.py<br/>thread pool, every N s] --> C[checker.py<br/>HTTP check + timing]
        C --> DET[detector.py<br/>debounce, no duplicates]
    end

    subgraph Agent["AI agent (LangGraph)"]
        G[graph.py<br/>StateGraph] --> T[tools/*<br/>http, dns, tcp, ssl, latency, history]
        G --> L[GPT-4.1 mini<br/>or offline mock]
    end

    C -->|HTTP| D & P
    T -->|diagnostics| D & P
    DET -->|incident opened| G
    G -->|issue, comments, close| GH[(GitHub REST API)]

    DB[(SQLite<br/>checks, incidents,<br/>tool_runs, events)]
    CP[(SQLite<br/>LangGraph checkpoints)]
    C --> DB
    G --> DB
    G <--> CP

    UI[Streamlit dashboard :8501] --> DB
    UI -->|approve / reject / verify / simulate| G
    API[FastAPI :8000] --> DB
    API -->|approve / reject / verify| G
```

## Agent graph

```mermaid
flowchart TD
    START([start]) --> triage
    triage --> investigate
    investigate -->|LLM requested tools| tools
    tools --> investigate
    investigate -->|enough evidence or max turns| analyze
    analyze --> report
    report --> approval_gate
    approval_gate -->|approved| create_ticket
    approval_gate -->|rejected| reject
    reject --> END1([end])
    create_ticket --> await_recovery
    await_recovery -->|Verify Recovery / auto-verify| verify_recovery
    verify_recovery -->|still failing| await_recovery
    verify_recovery -->|healthy| END2([end])

    classDef pause fill:#fff4d6,stroke:#d28e05
    class approval_gate,await_recovery pause
```

Yellow nodes pause the graph with a LangGraph `interrupt()`. The state is saved by `SqliteSaver`
(thread id `incident-<id>`), so the dashboard, the API or the CLI can resume it later, even after a restart.

| Node | What it does |
|---|---|
| `triage` | Loads the incident and triggering checks, writes the investigation brief, sets status `INVESTIGATING` |
| `investigate` | The LLM (bound to 9 tools) decides which diagnostics to run next, or stops |
| `tools` | Runs the requested tools in parallel and logs each call to `tool_runs` |
| `analyze` | Structured output → `Diagnosis` (ranked causes + evidence, SEV1–SEV4, impact, actions, confidence) |
| `report` | Renders the Markdown incident report |
| `approval_gate` | Human-in-the-loop: waits for Approve/Reject (skipped if `REQUIRE_HUMAN_APPROVAL=false`) |
| `create_ticket` | GitHub issue with labels `incident`, `severity:SEVx`, `site:<name>` (or dry-run payload) |
| `await_recovery` | Waits until someone (or the monitor's auto-verify) asks for a recovery check |
| `verify_recovery` | Re-checks the site: healthy → `RESOLVED`, comment + close issue; else comment and wait again |

## Incident lifecycle

```mermaid
stateDiagram-v2
    [*] --> OPEN: failure_threshold consecutive failed checks
    OPEN --> INVESTIGATING: agent starts
    INVESTIGATING --> AWAITING_APPROVAL: diagnosis + report ready
    AWAITING_APPROVAL --> TICKET_CREATED: approved
    AWAITING_APPROVAL --> REJECTED: rejected
    TICKET_CREATED --> TICKET_CREATED: verify - still failing
    TICKET_CREATED --> RESOLVED: verify - healthy
    RESOLVED --> [*]
    REJECTED --> [*]
```

## Data model (SQLite)

| Table | Purpose |
|---|---|
| `sites` | Configured targets (synced from `config/sites.yaml`) |
| `checks` | Every health check: status, HTTP code, response time, error type |
| `incidents` | Status, severity, summary, diagnosis JSON, Markdown report, GitHub issue number/URL/payload |
| `tool_runs` | Every diagnostic tool call by the agent: arguments, result, duration, turn |
| `incident_events` | Audit timeline (opened, agent decisions, diagnosed, approved, ticket, resolved…) |

## Offline / degraded modes

| Missing | Behaviour |
|---|---|
| `OPENAI_API_KEY` | `mock_llm.py` acts as the LLM: picks tools in rounds based on earlier results and applies the same severity rules |
| `GITHUB_TOKEN` (or `GITHUB_DRY_RUN=true`) | Issue payload is stored and shown in the dashboard instead of calling GitHub |
| GitHub API error | Logged as a `TICKET_FAILED` event, payload kept locally, workflow continues |

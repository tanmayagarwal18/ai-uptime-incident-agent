# Live demo walkthrough (about 3 minutes)

## Before the demo (once)

```bash
pip install -r requirements.txt
cp .env.example .env            # add GITHUB_TOKEN (Models: read + Issues: read/write) for real GPT-4.1 mini
python scripts/check_ai.py      # confirms the AI connection before you present
python scripts/seed_demo_data.py --reset   # 24 h of history so the charts look alive
```

Open three terminals:

| Terminal | Command | Port |
|---|---|---|
| 1 | `make demo-server` (or `python -m uvicorn demo.flaky_server:app --port 8001`) | 8001 |
| 2 | `make monitor` (or `python scripts/run_monitor.py`) | - |
| 3 | `make dashboard` (or `python -m streamlit run dashboard/streamlit_app.py`) | 8501 |

Optional 4th: `make api` → Swagger UI at http://127.0.0.1:8000/docs

## The demo

| Time | Do | Say |
|---|---|---|
| 0:00 | **Overview** page | "We monitor five sites, two local demo endpoints and three public APIs. KPIs, uptime and latency update live." |
| 0:20 | **Site detail** → `demo-homepage` | "Latency over 24 h with the threshold line. This dip six hours ago became a SEV3 incident the agent handled." |
| 0:40 | Sidebar **Demo control** → mode `error_500` → **Simulate incident** | "I'll break the Orders API: it now returns 500s. Two consecutive failures open an incident, and the AI agent takes over." |
| 1:00 | **Agent investigation trace** tab | "The agent chose its tools itself. Round 1: confirm the failure, check history, DNS and TCP. All healthy at the network layer, so round 2 inspects headers and samples latency." |
| 1:30 | **Root cause & actions** tab | "Structured diagnosis: database connection pool exhausted, SEV1 because it's a critical service with every request failing. Ranked causes, evidence, ordered actions." |
| 1:50 | **Approve & create ticket** | "Sensitive actions need human approval. The LangGraph run was paused and now resumes to create the GitHub issue with severity labels." |
| 2:10 | **Open GitHub issue** (or the GitHub tab in dry-run) | "The full report is in the issue." |
| 2:25 | Sidebar **Fix the demo site** → **Verify recovery** | "After the fix, the agent re-checks the site, marks the incident RESOLVED, comments on the issue and closes it." |
| 2:50 | **Timeline** tab | "Every step is audited, from detection to resolution." |

### Variations
- `error_503` → overload diagnosis using the `Retry-After` header
- `slow` → DEGRADED / SEV3 performance incident
- `timeout` → hung application (takes about 20 s because the checks really time out)
- Stop terminal 1 (Ctrl+C) → connection refused → "service process down" (SEV1)

### If the internet or the free AI limit fails mid-demo
Nothing breaks: the incident is finished by the offline demo AI and the incident page shows
"Analysed by: Offline demo AI (fallback: ...)" with the reason. Keep presenting.

### Terminal-only fallback
```bash
python scripts/simulate_incident.py --mode error_500 --approve --heal
```

### Narration (5 lines)
1. "Traditional monitors alert you; this one also investigates."
2. "When a site fails, a LangGraph agent powered by GPT-4.1 mini decides which diagnostics to run: DNS, TCP, TLS, HTTP and latency."
3. "It turns the evidence into a ranked root cause, a SEV1–SEV4 severity and concrete actions, written up as a report."
4. "With human approval it opens a GitHub issue, then verifies recovery and closes the loop automatically."
5. "Everything is persisted in SQLite and visible in the dashboard, so the engineer starts with answers, not just an alert."

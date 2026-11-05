# Documentation index

Start at the [README](../README.md). Pick the path that matches your role.

## By audience
| If you are… | Read |
|---|---|
| A recruiter or HR professional | [HR_PROJECT_BRIEF.md](HR_PROJECT_BRIEF.md) → [PROJECT_HIGHLIGHTS.md](PROJECT_HIGHLIGHTS.md) |
| A hiring engineer / interviewer | [README](../README.md) → [ARCHITECTURE.md](ARCHITECTURE.md) → [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md) → [RAG_AND_RETRIEVAL.md](RAG_AND_RETRIEVAL.md) → [ENGINEERING_METRICS.md](ENGINEERING_METRICS.md) |
| A developer setting up | [README § Local setup](../README.md#local-setup) → [TESTING.md](TESTING.md) → [API_OVERVIEW.md](API_OVERVIEW.md) |
| An operator | [DEPLOYMENT.md](DEPLOYMENT.md) → [runbooks/backup-restore.md](runbooks/backup-restore.md) → [runbooks/sentry-setup.md](runbooks/sentry-setup.md) → [SMOKE_CHECKLIST.md](SMOKE_CHECKLIST.md) |
| A security reviewer | [SECURITY.md](SECURITY.md) → [EVALUATION.md § Safety](EVALUATION.md#6-safety-evaluation) |

## Product and architecture
| Document | Contents |
|---|---|
| [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md) | Problem, users, capabilities, limitations |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System context, layers, persistence, jobs, deployment, trade-offs |
| [WORKFLOWS.md](WORKFLOWS.md) | The eight workflows: routes, storage, citations, limits |
| [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md) | LangGraph supervisor, specialists, ReAct tools |
| [RAG_AND_RETRIEVAL.md](RAG_AND_RETRIEVAL.md) | Embeddings, chunking, retrieval, citations, disabled features |
| [API_OVERVIEW.md](API_OVERVIEW.md) | Endpoint groups, auth, errors, request ids, rate limits |

## Quality, security and operations
| Document | Contents |
|---|---|
| [SECURITY.md](SECURITY.md) | Controls and known risks |
| [TESTING.md](TESTING.md) | Test suite, CI, gaps |
| [EVALUATION.md](EVALUATION.md) | Ten evaluation layers with honest status |
| [ENGINEERING_METRICS.md](ENGINEERING_METRICS.md) | Verified metrics, benchmark methods, quote-safety |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Vercel, Render, Supabase, Qdrant, R2, Docker |

## Recruiter-facing
[HR_PROJECT_BRIEF.md](HR_PROJECT_BRIEF.md) · [PROJECT_HIGHLIGHTS.md](PROJECT_HIGHLIGHTS.md)

## Engineering records (maintained by the phase workflow)
| Document | Contents |
|---|---|
| [PROJECT_STATE.md](PROJECT_STATE.md) | Current phase, open risks, backlog, handoff |
| [DECISIONS.md](DECISIONS.md) | Architecture decision records (ADR-001 onwards) |
| [audit/2026-11-02-architecture-audit.md](audit/2026-11-02-architecture-audit.md) | Baseline audit (partly superseded; see PROJECT_STATE) |
| [phase-reports/](phase-reports/) | One report per delivered phase/slice |
| [DOCUMENTATION_AUDIT_REPORT.md](DOCUMENTATION_AUDIT_REPORT.md) | Evidence and validation record for this documentation pass |

## Older root-level documents
[../ARCHITECTURE.md](../ARCHITECTURE.md) (original architecture note) · [../DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) (first-deploy walkthrough) ·
[../screenshots/README_SCREENSHOTS.md](../screenshots/README_SCREENSHOTS.md) (screenshot provenance) · [../evals/README.md](../evals/README.md) (workflow evaluation harness).

## Evidence labels used throughout
IMPLEMENTED · ENABLED · OFFLINE-TESTED · PRODUCTION-OBSERVED · PLANNED · UNVERIFIED — defined in [ARCHITECTURE.md](ARCHITECTURE.md).

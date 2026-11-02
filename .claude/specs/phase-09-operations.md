# Phase 09 — Operations

Status: STUB  ·  Related audit sections: P, U#12–17

## Goal
A deployable, observable and recoverable service.

## Candidate scope
- Readiness endpoint checking DB and Qdrant; keep `/health` as liveness (`src/api/main.py`).
- Run migrations once per deploy (today: Docker `CMD` and `lifespan` per worker).
- Backup/restore runbook for Postgres and Qdrant.
- Make CI lint/format blocking; re-enable or remove the disabled live-smoke job (`.github/workflows/ci.yml`).
- Dependency cleanup (no direct use found: passlib, tenacity, youtube-transcript-api, tavily-python, langchain
  meta-package) — ADR.
- docker-compose: pin the `qdrant` image tag, remove the unused `redis` service, avoid publishing DB ports;
  frontend API base as a build arg per environment.
- Decide whether `/docs` is exposed in production.

## Candidate acceptance criteria
- [ ] Readiness fails when DB or Qdrant is down.
- [ ] Backup + restore documented and tested once.

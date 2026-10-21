# CI shablonlari (GitHub Actions)

GitHub qayta ishlatiladigan workflow'larni faqat `.github/workflows/` dan chaqira oladi, shuning uchun
umumiy mantiq o'sha yerda, bu papkada esa servislar uchun **chaqiruvchi shablon** turadi (ADR-0009).

| Fayl | Vazifasi |
|---|---|
| `.github/workflows/platform.yml` | Kontraktlar, libs, infra konfiguratsiyasi, shablon smoke-test, gitleaks |
| `.github/workflows/_service-python.yml` | Python servis: lint (ruff, mypy) → test (≥70%) → contracts → build/deploy |
| `.github/workflows/_service-go.yml` | Go servis: gofmt, vet, golangci-lint → test (≥70%) → contracts → build/deploy |
| `.github/workflows/_contracts.yml` | gen-events --check, check-contracts, check-registry |
| `.github/workflows/_build-deploy.yml` | Image (tag = commit SHA) → migrate → deploy → health |
| `ci-templates/service-workflow.yml` | `scripts/new-service.sh` dan `.github/workflows/svc-<servis>.yml` |
| `scripts/deploy-service.sh` | Serverda: pull → migrate → `up -d --no-deps` → health, yiqilsa loglar + exit 1 |

## Muhitlar
- `dev` branch → `dev` environment, avtomatik.
- `main` branch → `prod` environment — GitHub'da *Required reviewers* yoqiladi (qo'lda tasdiqlash).

## Environment secrets (har muhitda)
`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. Image registry — GHCR
(`GITHUB_TOKEN`). Servis `.env` va `secrets/` serverda (`/opt/medbron/<servis>`), CI ularga tegmaydi.

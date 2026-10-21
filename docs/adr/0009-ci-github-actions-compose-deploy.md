# ADR-0009: CI — GitHub Actions, deploy — Docker Compose, registry — GHCR

- **Holat:** Qabul qilingan (1-bosqich; foydalanuvchi 2026-10-21 da tasdiqladi) · 2026-10-21

## Kontekst
BLUEPRINT GitLab CI yoki GitHub Actions ni va Docker Compose deploy'ni ko'zda tutadi. Repo GitHub'da
(`.github/workflows/ci.yml` mavjud); monolitda `k8s/` manifestlari bor. 0-bosqichdagi ochiq savollarga
(CI, deploy maqsadi) javob kelmadi — standart qaror qabul qilindi, keyin qayta ko'rib chiqilishi mumkin.

## Qaror
- **CI — GitHub Actions.** Umumiy mantiq qayta ishlatiladigan workflow'larda (`.github/workflows/_*.yml`),
  har servisda faqat chaqiruvchi (`svc-<servis>.yml`, `ci-templates/service-workflow.yml` dan). GitHub
  reusable workflow'larni faqat `.github/workflows` dan chaqira olgani uchun `ci-templates/` da faqat
  chaqiruvchi shablon turadi.
- **Bosqichlar:** lint → test (integratsiya bilan, coverage ≥ 70%) → contracts-check → build
  (tag = commit SHA) → migrate (alohida qadam) → deploy → health. `dev` → dev avtomatik, `main` → prod
  (GitHub environment, qo'lda tasdiqlash).
- **Deploy — Docker Compose** serverda (`scripts/deploy-service.sh`): `pull` → migrate profili →
  `up -d --no-deps` → har konteyner `healthy` bo'lguncha kutish; yiqilsa loglar va job qizil.
- **Registry — GHCR** (`ghcr.io/<owner>/medbron-<servis>:<sha>`).

## Oqibatlar
- (+) Servislar pipeline'i farqlanib ketmaydi; o'zgarish bir joyda.
- (+) Kubernetes'ga o'tish ochiq qoladi: image va health kontrakti bir xil, faqat deploy qadami almashadi.
- (−) Compose bitta hostga mo'ljallangan; gorizontal masshtab uchun keyinchalik k8s/Swarm ADR kerak.

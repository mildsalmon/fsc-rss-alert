# Slice-2 Epic — AWS EC2 운영 배포

> Source of truth: `docs/DESIGN-v2.md`의 Phase 2 / Distribution Plan / Terraform 섹션.

## 목표

로컬 `launchd` 의존을 제거하고, AWS 서울 리전 EC2에서 Docker 배치 컨테이너를 cron으로 실행한다.
`main` 머지 시 GitHub Actions가 이미지를 ECR에 push하고, EC2 cron은 실행 직전에 `:latest`를 pull한다.
상태는 EC2 호스트의 `/data/feed-collector/feed.db`에 영속화한다.

## 작업 목록

| ID | 제목 | 산출물 | 상태 |
|----|------|--------|------|
| S2-T1 | Docker 이미지 패키징 | `Dockerfile`, `.dockerignore` | DONE |
| S2-T2 | ECR publish workflow | `.github/workflows/publish-ecr.yml` | DONE |
| S2-T3 | Terraform AWS 리소스 | `infra/aws/*.tf` | DONE |
| S2-T4 | EC2 bootstrap cron | `infra/aws/user_data.sh.tftpl` | DONE |
| S2-T5 | 원격 state 가이드 | `infra/aws/backend.example.hcl`, README | DONE |
| S2-T6 | 운영 런북 | `infra/aws/README.md`, root README 링크 | DONE |
| S2-T7 | 실제 AWS apply/cutover | Terraform apply, GitHub variable 설정, 다음 poll/digest 관찰 | TODO |

## 인프라 범위

- ECR private repository + lifecycle policy.
- GitHub Actions OIDC provider/role for ECR push.
- EC2 instance role/profile for ECR pull.
- Optional SSM Parameter Store read permission for Slack token parameter name.
- Security group with outbound-only default; SSH ingress is opt-in.
- EC2 user_data installs Docker/cron/ECR credential helper and registers poll/digest cron.
- Terraform state uses an S3 backend with native lockfile support, not DynamoDB locking.

## 운영 방식

- Poll: every 20 minutes, `python -m feed_collector poll --db-path /data/feed.db --lock-file /data/feed.lock`.
- Digest: every day 09:00 KST.
- Container state: `/data/feed-collector` bind mount.
- Secrets: Terraform state에 저장하지 않는다. EC2 `/opt/feed-collector/.env` 또는 SSM SecureString parameter name만 사용.

## 완료 정의

1. `terraform plan`이 ECR/EC2/IAM/SG 변경을 보여준다.
2. GitHub Actions가 `main` push 또는 manual dispatch로 ECR에 `latest`와 SHA tag를 push한다.
3. EC2에서 `/opt/feed-collector/run.sh poll`이 컨테이너를 pull/run하고 sqlite를 `/data/feed-collector/feed.db`에 쓴다.
4. 다음 scheduled poll/digest가 Slack에 정상 동작한다.

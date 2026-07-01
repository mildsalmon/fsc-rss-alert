# AWS EC2 Deployment

This stack runs Feed Collector on one EC2 instance as cron-triggered Docker batch jobs.

## What Terraform Creates

- Private ECR repository with lifecycle policy that keeps only the latest 3 images.
- GitHub Actions OIDC role that can push to the ECR repository.
- EC2 instance role/profile with ECR read and Systems Manager Session Manager permissions.
- Optional EC2 permission to read one SSM Parameter Store SecureString for `SLACK_BOT_TOKEN`.
- Security group with outbound access; SSH ingress only when `ssh_ingress_cidr_blocks` is set.
- Amazon Linux 2023 EC2 instance bootstrapped with Docker, cron, ECR credential helper, and `/opt/feed-collector/run.sh`.
- EC2 public IPv4 is enabled by default for simple outbound egress.

## State Backend

Create an S3 bucket for Terraform state first, then copy `backend.example.hcl`.
The backend uses S3 native lockfile support, so a DynamoDB lock table is not required.

```bash
cp infra/aws/backend.example.hcl infra/aws/backend.hcl
$EDITOR infra/aws/backend.hcl
```

Initialize Terraform:

```bash
cd infra/aws
terraform init -backend-config=backend.hcl
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

## Variables

Start from:

```bash
cp infra/aws/terraform.tfvars.example infra/aws/terraform.tfvars
$EDITOR infra/aws/terraform.tfvars
```

Public IPv4 is not required for inbound access because SSH ingress is disabled unless explicitly configured.
It is enabled by default because the batch job needs outbound HTTPS access to pull from ECR, fetch RSS pages, and send Slack messages, and the default VPC usually does not include a NAT gateway.
If the selected subnet already has outbound egress through NAT, VPC endpoints plus internet egress, IPv6 egress, or another route, you can disable public IPv4:

```hcl
associate_public_ip_address = false
```

Using public IPv4 is simpler, but it can add fixed hourly IPv4 charges. Inbound access still remains closed unless `ssh_ingress_cidr_blocks` is configured.

Session Manager is enabled through the EC2 instance profile, so SSH keys are optional.
After Terraform applies the role attachment, wait a few minutes, then use:

```bash
aws ssm start-session --target "$(terraform output -raw instance_id)"
```

The AWS console path is `Systems Manager` -> `Session Manager` -> `Start session`.
If the instance does not appear immediately, confirm that it has outbound internet access and wait for SSM Agent registration.

Secrets are intentionally not Terraform variables. Use one of these:

- Set `slack_bot_token_parameter_name` to an existing SSM SecureString parameter name.
- Or configure SSH access, then write `/opt/feed-collector/.env` manually on the instance:

```bash
sudo install -m 600 /opt/feed-collector/.env.example /opt/feed-collector/.env
sudoedit /opt/feed-collector/.env
```

## GitHub Actions

After `terraform apply`, set this GitHub repository variable:

```bash
gh variable set AWS_ROLE_TO_ASSUME --body "$(terraform output -raw github_actions_role_arn)"
```

The workflow uses:

- `AWS_REGION=ap-northeast-2`
- `ECR_REPOSITORY=feed-collector-prod`

Change `.github/workflows/publish-ecr.yml` if you change `project_name` or `environment`.

## Runtime

EC2 cron runs in Asia/Seoul timezone:

- Poll: every 20 minutes.
- Digest: every day at 09:00.

Logs:

```bash
sudo tail -f /var/log/feed-collector/poll.log
sudo tail -f /var/log/feed-collector/digest.log
```

Manual smoke test on EC2:

```bash
sudo /opt/feed-collector/run.sh poll --dry-run --source fsc-press
sudo /opt/feed-collector/run.sh poll
sudo /opt/feed-collector/run.sh digest
```

State database:

```text
/data/feed-collector/feed.db
```

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "ap-northeast-2"
}

variable "project_name" {
  description = "Short project name used in AWS resource names."
  type        = string
  default     = "feed-collector"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"
}

variable "github_repository" {
  description = "GitHub repository in owner/name form."
  type        = string
  default     = "mildsalmon/fsc-rss-alert"

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repository))
    error_message = "github_repository must be in owner/name form."
  }
}

variable "github_branch" {
  description = "Branch allowed to assume the GitHub Actions ECR push role."
  type        = string
  default     = "main"
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN. Leave null to create one in this stack."
  type        = string
  default     = null
}

variable "github_oidc_thumbprints" {
  description = "Thumbprints for token.actions.githubusercontent.com when this stack creates the OIDC provider."
  type        = list(string)
  default     = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

variable "vpc_id" {
  description = "VPC id. Leave null to use the default VPC."
  type        = string
  default     = null
}

variable "subnet_id" {
  description = "Subnet id for the EC2 instance. Leave null to use the first subnet in the selected VPC."
  type        = string
  default     = null
}

variable "associate_public_ip_address" {
  description = "Whether to associate a public IPv4 address with the EC2 instance. Keep true for simple outbound egress in a public subnet; set false only when the subnet has NAT, VPC endpoints, IPv6 egress, or another outbound path."
  type        = bool
  default     = true
}

variable "instance_type" {
  description = "EC2 instance type."
  type        = string
  default     = "t3.micro"
}

variable "key_name" {
  description = "Optional EC2 key pair name for SSH access."
  type        = string
  default     = null
}

variable "ssh_ingress_cidr_blocks" {
  description = "CIDR blocks allowed to SSH to the instance. Empty means no SSH ingress."
  type        = list(string)
  default     = []
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GiB."
  type        = number
  default     = 12
}

variable "poll_cron_expression" {
  description = "Cron schedule for poll in Asia/Seoul timezone."
  type        = string
  default     = "*/20 * * * *"
}

variable "digest_cron_expression" {
  description = "Cron schedule for digest in Asia/Seoul timezone."
  type        = string
  default     = "0 9 * * *"
}

variable "slack_bot_token_parameter_name" {
  description = "Optional SSM SecureString parameter name containing SLACK_BOT_TOKEN. The token value is not managed by Terraform."
  type        = string
  default     = null
}

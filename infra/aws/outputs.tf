output "ecr_repository_url" {
  description = "ECR repository URL used by EC2 and GitHub Actions."
  value       = aws_ecr_repository.app.repository_url
}

output "github_actions_role_arn" {
  description = "Set this as the GitHub repository variable AWS_ROLE_TO_ASSUME."
  value       = aws_iam_role.github_actions_ecr_push.arn
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.app.id
}

output "instance_public_ip" {
  description = "EC2 public IP when associate_public_ip_address is true."
  value       = aws_instance.app.public_ip
}

output "ssh_command" {
  description = "SSH command when key_name and ssh ingress are configured."
  value       = aws_instance.app.public_ip == "" ? null : "ssh ec2-user@${aws_instance.app.public_ip}"
}

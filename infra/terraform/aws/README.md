# Terraform — AWS reference stack

One bank's dedicated stack (decision D1) in its chosen region. Modules:

| Module | Creates |
|---|---|
| `modules/keys` | The KMS keys: data (envelope KEK for tenant data keys), database, storage, audit, logs. Rotation on. |
| `modules/network` | VPC, private subnets across three AZs, NAT for egress through the allowlisting proxy, VPC endpoints (S3, KMS, Secrets Manager) so those calls never leave AWS. |
| `modules/database` | PostgreSQL 16 (Multi-AZ, encrypted with the database key, PITR 35 days, pgvector allowed, IAM auth, deletion protection, Performance Insights) and its parameter group. |
| `modules/storage` | Buckets: `attachments` (versioned, SSE-KMS) and `audit-exports` (Object Lock in compliance mode — WORM), all public access blocked, TLS-only policies. |
| `stack` | Composes the modules for one environment, plus the Secrets Manager secret the Helm chart reads through the External Secrets Operator. |

The Kubernetes cluster (EKS) is expected to exist (many banks run their own platform team's cluster);
`stack` takes its node security group to open Postgres to it.

```sh
cd stack
terraform init
terraform plan -var-file=../examples/prod-bank.tfvars
```

RPO ≤ 15 min comes from PITR (continuous WAL); RTO ≤ 4 h is the restore drill in
`docs/operations/dr-drill.md`.

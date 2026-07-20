# Security Review - 2026-06-04

This document records a point-in-time review of the repository. It is
historical evidence, not a guarantee that the current tree has the same
findings or tool results.

## Scope

Local non-supply-chain scans were run with:

- `cfn-lint`
- `checkov`
- `trivy`
- explicit local `semgrep` checks

Python package vulnerability scanners were intentionally excluded from this
review pass.

## Results At The Time Of Review

- `cfn-lint` passed with no CloudFormation syntax or schema findings.
- `trivy` secret scanning found no committed secrets.
- `trivy` misconfiguration scanning reported 11 CloudFormation findings:
  1 high and 10 low.
- `checkov` reported 30 passed checks and 13 failed CloudFormation benchmark
  checks.
- Local `semgrep` checks found no `0.0.0.0/0` exposure.

## Production-Hardening Considerations

The review identified the following items:

- Aurora storage encryption used an AWS-managed key instead of a
  customer-managed KMS key.
- Secrets Manager secrets used AWS-managed encryption instead of a
  customer-managed KMS key.
- The CloudWatch log group used default encryption instead of a
  customer-managed KMS key.
- RDS IAM database authentication was not enabled.
- RDS enhanced monitoring and Performance Insights were not enabled.
- Some security-group rules lacked descriptions.
- The Lambda function did not enable X-Ray tracing, reserved concurrency, or a
  dead-letter queue.

These findings primarily concern production monitoring, encryption controls,
and benchmark hardening. The CloudFormation stack in this repository is
intended for disposable dev/test use.

## Revalidation

Re-run the selected scanners against the current commit before relying on these
results. Record tool versions and exact commands in the next review so that the
results are reproducible.

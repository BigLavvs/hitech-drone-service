# Security reporting

Report suspected vulnerabilities privately to **hello@oluwapelumi.xyz**.
Do not put exploit details, credentials, tokens, signed URLs or customer data in
public issues or pull requests.

Include the affected commit, a description of the impact, and minimal reproduction
steps using synthetic data. Remove secrets from screenshots and logs. Coordinate
privately before sending sensitive proof-of-concept material.

This is an assessment/portfolio application, not a supported commercial release.
Reports should target the current `main` version. No response-time or remediation
SLA is promised. Disclosure timing should be agreed with the maintainer.

Only test systems and data you own or are explicitly authorized to assess. Do not
run destructive tests against the deployed demo or other users' data.

## Deployment responsibilities

- Disable `ENABLE_DEMO_AUTH` outside the assessment demonstration.
- Keep Django, database, R2 and JWT signing secrets outside source control.
- Use HTTPS, private object storage, restricted database access and separate test
  infrastructure. Never run the test suite with production database credentials.
- Monitor worker/beat health, failed jobs, storage usage and dependency updates.
- Rotate exposed credentials; removing a value from the current source does not
  remove it from Git history or invalidate it.

Implemented controls and remaining operational limitations are documented in
[README.md](README.md). The presence of tests is not a security certification.

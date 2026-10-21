# Workflows rules

Applies to: `.github/workflows/**`.

- Declare minimal top-level `permissions` and grant more per job only when needed.
- Run project commands through `make` targets, not inline scripts.
- Use `concurrency` to cancel superseded runs on pull requests.
- Never run self-hosted runners for fork pull requests.
- Discover modules from `solution.yaml` and `modules/*/module.yaml`; do not hard-code module names or use the descriptive `type` field as a behavior switch.
- Keep fork pull requests build/test-only and read-only. Package publication, state updates, tagging, promotion, and cleanup require trusted jobs with narrowly scoped permissions and protected environments.
- Module and solution release versions are calculated from the documented lifecycle; workflows must be idempotent and promote immutable OCI digests without rebuilding.
- Publication uses successful same-repository push CI, trusted default-branch tooling, a separate exact-source checkout, and the protected `release-publication` environment. Keep approved impacts on the trusted tooling revision and state on `automation/release-state`; never execute source-branch release scripts with write credentials.
- Validate with `actionlint`.

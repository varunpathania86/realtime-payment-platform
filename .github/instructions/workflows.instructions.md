---
applyTo: ".github/workflows/**"
---
- Declare minimal top-level `permissions` and grant more per job only when needed.
- Run project commands through `make` targets, not inline scripts.
- Use `concurrency` to cancel superseded runs on pull requests.
- Never run self-hosted runners for fork pull requests.
- Validate with `actionlint`.

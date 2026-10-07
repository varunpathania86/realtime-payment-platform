---
applyTo: "**/Makefile,**/*.mk"
---
- Provide `build`, `test`, `lint`, `fmt`, `clean`, and `help`; mark them `.PHONY`.
- Document each target with a `## description` comment so `make help` lists it.
- Use tabs for recipe indentation.
- Targets must be idempotent, or clearly say they are destructive.
- Sub-project Makefiles start from `templates/project-makefile.mk`.

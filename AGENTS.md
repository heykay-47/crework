# Repository guidance

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `heykay-47/crework`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical triage labels without aliases. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.

## Container workflow

Build with `docker build -t client-feedback-triage .`. Run the image as the host UID/GID with `GEMINI_API_KEY` injected at runtime and bind mounts for `/input` (read-only), `/context` (read-only), and `/output`. See `README.md` for the exact command.

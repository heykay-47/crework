#!/usr/bin/env bash
#
# Record the third, write-enabled step of the frozen acceptance cycle.
#
# This wraps the real `run --reanalyze` container command in an asciinema
# recording so the submission GIF and screenshots come from the actual
# session rather than from generated artwork. You drive the Approval prompts.

set -euo pipefail

REPOSITORY="${REPOSITORY:-heykay-47/crework-feedback-demo}"
OUTPUT_DIR="${OUTPUT_DIR:-output/acceptance-live}"
BASELINE_FILE="${BASELINE_FILE:-manual-baseline.json}"
CAST="${CAST:-.agent/capture/third-run.cast}"

if [[ ! -f "$BASELINE_FILE" ]]; then
  echo "missing $BASELINE_FILE; run .agent/wizards/manual-baseline.sh first" >&2
  exit 1
fi
if [[ ! -x .agent/bin/asciinema ]]; then
  echo "missing .agent/bin/asciinema" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a
GITHUB_TOKEN="$(gh auth token)"
export GITHUB_TOKEN

mkdir -p "$(dirname "$CAST")" "$OUTPUT_DIR"
rm -f "$CAST"

cat <<'INFO'

  Recording the third acceptance run.

  You will be asked to decide each admitted result:
    - the hero CTA copy change        -> approve
    - the mobile navbar overlap       -> approve
    - the contact submit anomaly      -> confirm the Manual Review, then approve

  Clarification Requests and Withheld Results are never offered.
  Three real Issues will be created in the demo repository.

INFO
read -r -p "  Press Enter to start recording. " _

.agent/bin/asciinema rec "$CAST" \
  --cols 100 --rows 32 \
  --command "docker run --rm -it --user $(id -u):$(id -g) \
    --env GEMINI_API_KEY --env GITHUB_TOKEN \
    --mount type=bind,src=$PWD/fixtures/canonical,dst=/input,readonly \
    --mount type=bind,src=$PWD/fixtures/canonical,dst=/context,readonly \
    --mount type=bind,src=$PWD/fixtures,dst=/app/fixtures,readonly \
    --mount type=bind,src=$PWD/$BASELINE_FILE,dst=/manual-baseline.json,readonly \
    --mount type=bind,src=$PWD/$OUTPUT_DIR,dst=/output \
    client-feedback-triage run /app/fixtures/canonical/feedback-recording.mp4 \
    --output /output --reanalyze \
    --repository $REPOSITORY \
    --manual-baseline /manual-baseline.json \
    --operator-label 'acceptance cycle'"

echo
echo "  recorded: $CAST"
echo "  next: tell the agent the run finished."

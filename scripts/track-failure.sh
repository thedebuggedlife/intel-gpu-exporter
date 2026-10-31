#!/usr/bin/env bash
# Reports this run on the one open tracking issue, opening it if there is none.
# A person closes the issue, which is what confirms the fix. Usage: scripts/track-failure.sh
set -euo pipefail

label=automation-failure
run_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
report="\`$GITHUB_WORKFLOW\` failed, timed out or was cancelled at \`$GITHUB_SHA\`: $run_url"

# The log still names the run if everything below fails
echo "::error::$report"

gh label create "$label" --repo "$GITHUB_REPOSITORY" --force \
    --description "A scheduled or release run needs a person" --color B60205 ||
    echo "couldn't create the label, so it has to exist already"

number=$(gh issue list --repo "$GITHUB_REPOSITORY" --label "$label" --state open \
    --json number --jq 'map(.number) | min // empty')

if [ -n "$number" ]; then
    gh issue comment "$number" --repo "$GITHUB_REPOSITORY" --body "$report"
else
    gh issue create --repo "$GITHUB_REPOSITORY" --label "$label" \
        --title "Automated run failed" \
        --body "$report

@avargaskun Close this issue once the cause is fixed. Later failures comment here until then."
fi

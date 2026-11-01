#!/usr/bin/env bash
# Reports a finished run on the one open tracking issue, opening it if there is none.
# A person closes the issue, which is what confirms the fix. Usage: scripts/track-failure.sh
set -euo pipefail

label=automation-failure
run_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$FAILED_RUN_ID"
report="\`$FAILED_WORKFLOW\` failed, timed out or was cancelled at \`$FAILED_SHA\`: $run_url"

# A timeout concludes cancelled too, and only a run dropped from the queue has no jobs
if [ "$FAILED_CONCLUSION" = cancelled ]; then
    jobs=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$FAILED_RUN_ID/jobs?filter=all" \
        --jq .total_count) || jobs=unknown
    if [ "$jobs" = 0 ]; then
        echo "$FAILED_WORKFLOW run $FAILED_RUN_ID started no job, so there is nothing to report"
        exit 0
    fi
fi

# The log still names the run if everything below fails
echo "::error::$report"

gh label create "$label" --repo "$GITHUB_REPOSITORY" --force \
    --description "A scheduled or release run needs a person" --color B60205 ||
    echo "couldn't create the label, so it has to exist already"

# Listed rather than searched, because search can miss an issue opened seconds ago
number=$(gh api "repos/$GITHUB_REPOSITORY/issues?labels=$label&state=open&sort=created&direction=asc" \
    --jq 'map(select(.pull_request | not)) | .[0].number // empty')

if [ -n "$number" ]; then
    gh issue comment "$number" --repo "$GITHUB_REPOSITORY" --body "$report"
else
    gh issue create --repo "$GITHUB_REPOSITORY" --label "$label" \
        --title "Automated run failed" \
        --body "$report

@avargaskun Close this issue once the cause is fixed. Later failures comment here until then."
fi

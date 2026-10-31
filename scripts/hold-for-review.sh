#!/usr/bin/env bash
# Takes a PR out of auto-merge and asks a person to review it.
# Usage: scripts/hold-for-review.sh <pr-number>
set -euo pipefail

number=$1

armed=$(gh pr view "$number" --repo "$GITHUB_REPOSITORY" --json autoMergeRequest \
    --jq '.autoMergeRequest != null')
# GitHub only disarms a PR when someone without write access pushes, and the app has write access
if [ "$armed" = true ]; then
    gh pr merge "$number" --repo "$GITHUB_REPOSITORY" --disable-auto
fi

gh api --method POST "repos/$GITHUB_REPOSITORY/pulls/$number/requested_reviewers" \
    --field 'reviewers[]=avargaskun' >/dev/null

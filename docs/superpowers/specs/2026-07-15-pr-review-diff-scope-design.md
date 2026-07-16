# PR review diff scope

## Goal

Prevent `code-review-c-pr.yml` from posting findings about unchanged code.

## Change

Modify `run-claude-review.sh` only. Its generated prompt will require every finding to cite a changed file and added-side line from the supplied PR patch. It will forbid repository-wide review and require `No findings.` when no diff-backed finding exists.

`code-review-c-pr.yml` remains unchanged: it already checks out the intended PR head and supplies the repository and PR number.

## Data flow

1. `gh pr diff` retrieves the PR patch.
2. The script builds the review prompt from instructions, focus text, and patch.
3. Claude returns only diff-backed findings or `No findings.`.
4. The script posts that output as the PR comment.

## Error handling

Existing retry, exit-status, and comment-posting behavior remains unchanged. Invalid model output is constrained by the prompt; no output filtering is added.

## Verification

Add a shell self-check that builds a minimal patch prompt and asserts the scope rules appear in it.

## Non-goals

- Reviewing unchanged surrounding code or call chains.
- Parsing or validating model findings after generation.
- Changing workflow dispatch, authentication, or review-comment publication.

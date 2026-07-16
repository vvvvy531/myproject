# PR Review Diff Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the Claude PR-review prompt permits findings only on lines added by the supplied patch.

**Architecture:** Keep the existing PR retrieval, retry, and comment publication flow. Add explicit scope rules directly beside the generated prompt format, then protect those rules with one dependency-free shell self-check. No workflow, diff parser, or response post-processor changes.

**Tech Stack:** Bash, `grep`, GitHub CLI, Claude Code CLI.

---

## File structure

- Modify: `.tmp-review-scripts/ci-scripts/run-claude-review.sh` — emits the PR review prompt.
- Create: `.tmp-review-scripts/ci-scripts/test-run-claude-review-prompt.sh` — verifies prompt scope requirements remain in the generator.
- No change: `.github/workflows/code-review-c-pr.yml` — already checks out the PR head and invokes the script from `source`.

### Task 1: Lock review findings to added patch lines

**Files:**
- Create: `.tmp-review-scripts/ci-scripts/test-run-claude-review-prompt.sh`
- Modify: `.tmp-review-scripts/ci-scripts/run-claude-review.sh:119-124`

- [ ] **Step 1: Write the failing shell self-check**

Create `.tmp-review-scripts/ci-scripts/test-run-claude-review-prompt.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run-claude-review.sh"

for rule in \
  'Review only added (`+`) lines in the DIFF BELOW; do not review unchanged repository code or deleted lines.' \
  'Every finding must cite a file and a new-side line number that exists in the DIFF BELOW.' \
  'If no high-confidence finding meets these rules, output exactly: No findings.'; do
  grep -Fq -- "$rule" "$script" || {
    printf 'Missing review-scope rule: %s\n' "$rule" >&2
    exit 1
  }
done
```

- [ ] **Step 2: Run the self-check and verify it fails**

Run:

```bash
bash .tmp-review-scripts/ci-scripts/test-run-claude-review-prompt.sh
```

Expected: exit `1`, first error starts with `Missing review-scope rule:`.

- [ ] **Step 3: Add the three scope rules to generated prompt**

In `.tmp-review-scripts/ci-scripts/run-claude-review.sh`, immediately after the existing line:

```bash
printf '%s\n' '- Skip style-only or low-confidence items'
```

add:

```bash
printf '%s\n' 'Review only added (`+`) lines in the DIFF BELOW; do not review unchanged repository code or deleted lines.'
printf '%s\n' 'Every finding must cite a file and a new-side line number that exists in the DIFF BELOW.'
printf '%s\n' 'If no high-confidence finding meets these rules, output exactly: No findings.'
```

Do not alter `AGENTS.md`, patch retrieval, the Claude CLI invocation, retries, or `gh pr comment`.

- [ ] **Step 4: Run shell syntax and scope checks**

Run:

```bash
bash -n .tmp-review-scripts/ci-scripts/run-claude-review.sh
bash .tmp-review-scripts/ci-scripts/test-run-claude-review-prompt.sh
```

Expected: both commands exit `0`; second emits no output.

- [ ] **Step 5: Review the exact diff**

Run:

```bash
git -C .tmp-review-scripts diff --check
git -C .tmp-review-scripts diff -- ci-scripts/run-claude-review.sh ci-scripts/test-run-claude-review-prompt.sh
```

Expected: `diff --check` exits `0`; diff changes only the three prompt lines plus the self-check.

- [ ] **Step 6: Commit review-script change**

Run:

```bash
git -C .tmp-review-scripts add ci-scripts/run-claude-review.sh ci-scripts/test-run-claude-review-prompt.sh
git -C .tmp-review-scripts commit -m "fix: scope Claude PR reviews to changed lines"
```

Expected: one commit in the `myproject-linux-builds` repository.

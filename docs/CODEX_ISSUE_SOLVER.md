# Codex Issue Solver

Automation entrypoint for solving `myproject-source` issues.

Flow:
GitHub issues webhook → n8n → repository_dispatch → `Codex Issue Solver` GitHub Action.

Security note:
- `myproject` is public.
- Do not upload source-code summaries, patches, logs, or artifacts from private `myproject-source`.
- PRs are created only inside private `myproject-source`.
- Public action logs should contain only high-level counts/status.

Goal:
> /goal 在仓库 myproject-source 中，自动修复 Issues 板块中的所有问题，并提交 Pull Request，在审核通过后将其合并到主分支。
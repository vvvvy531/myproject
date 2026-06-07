# Codex Issue Solver Tracking

This project uses GitHub webhook → n8n → repository_dispatch → GitHub Action to solve `myproject-source` issues with Codex.

Run summaries are stored as GitHub Actions artifacts:
- Workflow: `.github/workflows/codex-solve-issues.yml`
- Artifact name: `codex-solve-report-*`

n8n workflow:
- Name: `Codex Issue Solver - GitHub Monitor`
- ID: `LmJVVlF0JdM3Wcqe`

Trigger condition:
- `myproject-source` open issues count > 10

Goal:
> /goal 在仓库 myproject-source 中，自动修复 Issues 板块中的所有问题，并提交 Pull Request，在审核通过后将其合并到主分支。
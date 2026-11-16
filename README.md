# comp-runner
Runs the CRM "Comp" searches in GitHub Actions (no Mac needed). Reads queued jobs from the CRM's `comp-worker` function,
runs Codex (ChatGPT plan login stored in GitHub Secrets: `CODEX_AUTH_JSON`, `COMP_WORKER_SECRET`), posts the result back.
No secrets or customer addresses are stored in this repo or printed in its logs.

## If cloud runs fail with a login error
Delete the cached logins, then re-save the current Mac login:
```
gh cache delete --all --repo Mickyatakilt/comp-runner
gh secret set CODEX_AUTH_JSON --repo Mickyatakilt/comp-runner < "$HOME/.codex/auth.json"
```

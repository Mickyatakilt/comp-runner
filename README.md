# comp-runner
Runs the CRM "Comp" searches in GitHub Actions (no Mac needed). Reads queued jobs from the CRM's `comp-worker` function,
runs Codex (ChatGPT plan login stored in GitHub Secrets: `CODEX_AUTH_JSON`, `COMP_WORKER_SECRET`), posts the result back.
No secrets or customer addresses are stored in this repo or printed in its logs.

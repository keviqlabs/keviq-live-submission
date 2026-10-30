# Rules for everyone working in this repo

1. **Only new work.** This repository contains code written during the hackathon (from 2026-10-26 23:24 ICT). Do not copy, retype or consult files from earlier repositories. Allowed references: the tech plan, the day prompt, Meta developer docs.
2. **Commits** are in English and say what changed. Do not mention AI model or agent names in commits.
3. **No secrets** in code, logs, docs or screenshots. Real values (API keys, page tokens, IDs, server IPs, emails) live only in `.env` (gitignored). `deploy/.env.example` holds fake values. Run `make secrets` before every push.
4. **Data:** synthetic or Founder-approved only. Phone numbers in test data are fake. No real Messenger sends, no hiding/deleting real comments, no going live, no real orders until the Founder opens live test level 2.
5. **Money:** every Model API call emits a `model_call` event (model, tokens, cost, latency, purpose). Daily cap 30 USD, total 300 USD; report to the Founder at 20 USD/day. No Muse Wonder this week.
6. **Order permissions live in `orders/rules.py`.** No prompt can override them. Do not touch `rules.py` without a test. Invariants: no CONFIRMED order without customer confirmation; no price change, discount or refund without seller approval; no code path that collects money.
7. **Public text** (README, docs): no superlative or "we were early" claims, no customer names, no KXR results.
8. **Claims of "it ran"** in a PR come with the command and its output. Reviewer ≠ author.
9. **Contracts** (`contracts/`) change only via the contracts owner + Founder, with a `docs/DECISIONS.md` entry and a schema `v` bump when breaking.

10. **History hygiene.** `make hygiene` (also run by the workflow and by the `commit-msg` hook that `make hooks` installs) rejects tool names, internal role labels, decision codes and personal e-mail addresses in commit messages and tracked files. Merge a pull request with its written title as the merge-commit subject, never the default text that names the branch. Commit with the GitHub private address (`git config user.email`).

## Commands

```
make infra-up   # PostgreSQL via compose + migrations
make app        # API, SSE, console on :8000
make worker     # event workers
make replay SESSION=demo1
make check      # lint + tests + quick measure — must be green before a PR
make secrets    # secret scan (gitleaks)
```

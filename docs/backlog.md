# Backlog

This repo has no work tracker of its own. Its work items live in the cross-project Anthill installation
one tier up, at `E:\GameDev\Unity\.anthill\backlog\`, in the `dev` workstream.

## What is available in a session started here

- `E:\GameDev\Unity\CLAUDE.md` loads automatically (Claude Code loads `CLAUDE.md` from every ancestor
  directory), so the tier's safety invariants and backlog scope are in context.
- The `anthill` CLI is on PATH (`C:\Users\thewa\go\bin\anthill.exe`).
- The tier's skills (`submit`, `triage`, `dispatch`, `map-concept`, …) in
  `E:\GameDev\Unity\.claude\skills\` are **not** loaded here. Read a skill's `SKILL.md` directly when
  its procedure is needed.

## Working with items

- Read: browse `E:\GameDev\Unity\.anthill\backlog\dev\` and `intake\`.
- File: run `anthill backlog new --title "…" --value "…"` from `E:\GameDev\Unity\`.
- Routing rules: `E:\GameDev\Unity\.anthill\backlog\workstreams.md`.

Requests for mapgen changes that originate in Battlehounds may also sit in
`E:\GameDev\Unity\battlehounds\.anthill\backlog\`.

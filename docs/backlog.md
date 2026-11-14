# Backlog

This repo has no work tracker of its own. Its work items live in the cross-project Anthill installation
one tier up, at `E:\GameDev\Unity\.anthill\backlog\`, in the `mapgen` workstream.

## What is available in a session started here

- `E:\GameDev\Unity\CLAUDE.md` loads automatically (Claude Code loads `CLAUDE.md` from every ancestor
  directory), so the tier's safety invariants and backlog scope are in context.
- The `anthill` CLI is on PATH (`C:\Users\thewa\go\bin\anthill.exe`).
- The tier's skills (`submit`, `triage`, `dispatch`, `map-concept`, …) in
  `E:\GameDev\Unity\.claude\skills\` are **not** loaded here. Read a skill's `SKILL.md` directly when
  its procedure is needed.

## Working with items

- List: `anthill backlog list --workstream mapgen --root E:\GameDev\Unity`
- Show: `anthill backlog show <id> --root E:\GameDev\Unity`
- File: `anthill backlog new --title "…" --value "…" --hint mapgen --root E:\GameDev\Unity`
  (lands in `intake\`; triage moves it into `mapgen\`).
- Claim and close: `anthill backlog claim <id>`, then `anthill backlog close <id> --done`, both with
  the same `--root`.
- Item frontmatter is written only through the CLI. Schema and commands:
  `E:\GameDev\Unity\.anthill\backlog\bindings.md`.
- Routing rules: `E:\GameDev\Unity\.anthill\backlog\workstreams.md`.

Requests for mapgen changes that originate in Battlehounds may also sit in
`E:\GameDev\Unity\battlehounds\.anthill\backlog\`.

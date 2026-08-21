# House rules for agents in this repository

Every agent run reads this file at the start (`runtime._read_conventions`). It is kept
short: everything here costs context in every single run, so it only holds what the code
itself does not say.

## Scope: your ticket, nothing else

Change only what your ticket and your approved plan ask for. If you spot another bug along
the way, **write it into your result, do not fix it**. A ticket that touches unrelated
files becomes a merge conflict nobody wins, and it holds back the work it came for.

That is exactly what happened on 2026-08-07: a ticket about a failing job came back with a
rebuilt provider error path, because the agent mistook an error message in the comment
history for its assignment. Messages like these (worker restart, deadlock, truncated model
response) are **infrastructure mishaps, never your task**.

## Documentation lives in the vault, not in the repository

Project and stack knowledge is kept in Obsidian (`02 Projekte/…`, `03 Bereiche/…`). Do
**not** create notes, status documents or paths like `02 Projekte/...` inside the
repository. They drift against the vault version, and then nobody knows which one counts.
The repository holds code, tests, `README.md` and comments on the code.

## Language: English in the code

Comments, docstrings and strings are English, even though we talk German around it. Do not
translate technical terms that are more common in English (pull request, docker compose).
No em dashes anywhere: use a comma, a colon, parentheses or a full stop.

## Comments explain the why

The code says what happens. A comment says why it is this way and not another, preferably
with the case that led to the solution. Comments that retell the next line get flagged in
review.

## Error texts

An error that reaches a person goes out as `Fehler`, not as `HTTPException`:

    raise Fehler(404, "err.ticket_not_found", "Ticket not found")
    raise Fehler(409, "err.type_already_exists", "The type '{name}' already exists", name=key)

The English sentence is what the API answers; the key beside it is what the browser looks up
so a German interface says it in German. Both catalogs (`frontend/src/i18n/de.json` and
`en.json`) get the new key, with the same `{placeholders}` in both.

`HTTPException` is left for the cases that only pass a foreign message through (`str(exc)`)
and have no sentence of their own to name.

## The look of the interface

`frontend/DESIGN.md` says which building blocks a page is made of (`Bereich`, `Liste`,
`ListenZeile`, `Etikett`, `Zustand`, `Zeilenknopf` … in `src/components/ui.tsx`) and when
each one is taken. Read it BEFORE writing markup: whoever builds a chain of classes by hand
that already exists there produces exactly the differences that file exists to abolish — five
tabs of the same page had grown five answers to the same question.

## Building and checking

- `check` runs inside the worktree and has to be green before you report done.
- `npm run check:errortexts` (in `frontend/`) says whether every error key exists in both
  catalogs.
- No manual deploy: this project has no stack directory of its own, the deployer rejects the
  request. Changes go live through review and merge.
- Tests belong to the change, not to a follow-up ticket.

## When you get stuck

Ask (`ask_human`) instead of guessing, but only when the question really needs a human. If
your budget runs out, hand over cleanly (`continue_later`): what you learned, what is done,
what comes next.

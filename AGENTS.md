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

Comments, docstrings, strings **and commit messages** are English, even though we talk German
around it. Do not translate technical terms that are more common in English (pull request,
docker compose). No em dashes anywhere: use a comma, a colon, parentheses or a full stop.

Four things stay German on purpose, and none of them is a leftover:

- `frontend/src/i18n/de.json`, the German message catalog. That is what it is for.
- What the messenger bot writes to a person. It has no catalog yet, and English there would
  change what somebody reads on their phone, not what a developer reads in the source.
- Test DATA: mail subjects, chat texts, spam samples. Language material, not prose.
- Signal identifiers and context keys that already stand in stored data (the memory note
  names, `auftrag`/`ablage`/`still_wenn` in the job parameters). Renaming those is a data
  migration, not a translation.

## No names in the repository

This repository is public. Nothing in it names a person, a company, or a project that is not
this one:

- **No ticket keys.** A comment may keep its reasoning and its date, but not `ABC-32`: the
  number means nothing outside the installation that issued it. Write "one ticket", "a
  reviewer run", or just the date.
- **No neighbouring systems.** Other services of the same household are "the predecessor",
  "another program", "a game bot". Third party products that this genuinely talks to keep
  their names, because the tool names depend on them.
- **Neutral fixtures.** Test projects are `ABC`/`XYZ`, sample keys are `ABC-7`. They only
  ever had to be *some* key.
- Traccoon's own name, its `traccoon_*` tools and its repository URL stay.

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

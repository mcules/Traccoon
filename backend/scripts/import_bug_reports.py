"""Take the bug reports of another program over into Traccoon, once.

The program that first needed this carried its own reports: `bugs` (the matter), `bug_posts`
(first description plus replies, some of them internal) and `bug_post_images` (files under
`data/bug-images/<id>.<ext>`). From now on the reports live here, so the old ones have to
come along - a report whose history stays behind is only half a report.

What the script keeps: the reporter, every author, the internal flag, the kind, the state
and above all the times. A migrated conversation that all happened "today" is worthless for
understanding what somebody was told a year ago.

Run:
    docker compose exec backend python scripts/import_bug_reports.py \
        --export /tmp/bugs.json --images /tmp/bug-images --source <program> [--go]

Without `--go` it only says what it would do. Running it twice does not duplicate: a report
already imported is recognised by its id over there (field `foreign_ref`).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select                                    # noqa: E402

from app.db import SessionLocal                                  # noqa: E402
from app.models.artifact import Artifact, ArtifactField, ArtifactValue  # noqa: E402
from app.models.bugs import BugSource, ReportImage, ReportPost    # noqa: E402
from app.services import artifact_fields as fields_svc           # noqa: E402
from app.services import bugs as svc                             # noqa: E402

MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp"}


def when(millis: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(millis / 1000, tz=dt.timezone.utc)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True, help="JSON out of the other program (rows/posts/imgs)")
    parser.add_argument("--images", default="", help="Directory with the picture files")
    parser.add_argument("--source", required=True, help="Key of the reporting program")
    parser.add_argument("--go", action="store_true", help="Really write")
    args = parser.parse_args()

    data = json.loads(pathlib.Path(args.export).read_text(encoding="utf-8"))
    bugs, posts, images = data["rows"], data["posts"], data["imgs"]
    by_bug: dict[int, list] = {}
    for post in posts:
        by_bug.setdefault(post["bug_id"], []).append(post)
    by_post: dict[int, list] = {}
    for image in images:
        by_post.setdefault(image["post_id"], []).append(image)

    async with SessionLocal() as db:
        source = (await db.execute(select(BugSource).where(BugSource.key == args.source))
                  ).scalar_one_or_none()
        if source is None:
            print(f"No reporting program named {args.source!r}. Register it first.")
            return 1
        kind = await svc.ensure_type(db)
        fields = {f.key: f for f in await fields_svc.fields_of(db, kind.id, only_active=False)}
        if "foreign_ref" not in fields:
            print("The field `foreign_ref` is missing on the report type.")
            return 1
        there = await already_here(db, fields["foreign_ref"].id)

        neu = übernommen = 0
        for bug in bugs:
            marke = f"{args.source}:{bug['id']}"
            if marke in there:
                übernommen += 1
                continue
            neu += 1
            if not args.go:
                continue
            await one_report(db, source, kind, fields, bug, by_bug.get(bug["id"], []),
                             by_post, args, marke)

        print(f"{len(bugs)} reports in the export, {übernommen} already here, "
              f"{neu} {'written' if args.go else 'would be written'}")
        if not args.go:
            print("Nothing changed. With --go it happens.")
    return 0


async def already_here(db, field_id: int) -> set[str]:
    rows = (await db.execute(select(ArtifactValue.value_text)
                             .where(ArtifactValue.field_id == field_id))).scalars().all()
    return set(rows)


async def one_report(db, source, kind, fields, bug, posts, by_post, args, marke) -> None:
    """One report with its whole conversation. The first post is the description."""
    posts = sorted(posts, key=lambda p: p["id"])
    first = posts[0] if posts else None
    artifact = Artifact(
        type_id=kind.id, project_id=source.project_id, title=bug["title"][:500],
        status_key=svc.FOREIGN_STATUS.get(bug["status"], "new"),
        created_at=when(bug["created_at"]), updated_at=when(bug["updated_at"]),
    )
    db.add(artifact)
    await db.flush()

    values = {
        "status": svc.FOREIGN_STATUS.get(bug["status"], "new"),
        "kind": svc.FOREIGN_KIND.get(bug["kind"], "bug"),
        "app": source.key,
        "contact": bug["reporter"],
        "reporter_ref": str(bug["reporter_id"]),
        "foreign_ref": marke,
        "details": (first or {}).get("body", ""),
        "environment": args.source,
    }
    for key, value in values.items():
        if fields.get(key) is not None and value:
            await fields_svc.set_values(db, artifact.id, fields[key], [str(value)])

    # The first post is the description and stands in `details`; only what came afterwards
    # is conversation. Otherwise every migrated report would start by saying itself twice.
    # Its pictures do have to come along though, and they hang off the report: 18 of the 21
    # pictures over there were attached to a first description, and skipping the post silently
    # dropped them.
    if first is not None:
        for image in by_post.get(first["id"], []):
            await one_image(db, None, image, args, artifact=artifact)
    for post in posts[1:]:
        row = ReportPost(
            artifact_id=artifact.id, body=post["body"], internal=bool(post["internal"]),
            author_label=post["author"], external_ref=str(post["author_id"]),
            created_at=when(post["created_at"]), updated_at=when(post["created_at"]),
        )
        db.add(row)
        await db.flush()
        for image in by_post.get(post["id"], []):
            await one_image(db, row, image, args)
    await db.commit()


async def one_image(db, post, image, args, *, artifact=None) -> None:
    if not args.images:
        return
    path = pathlib.Path(args.images) / f"{image['id']}.{image['ext']}"
    if not path.exists():
        print(f"  picture {path.name} is missing, skipped")
        return
    raw = path.read_bytes()
    db.add(ReportImage(post_id=getattr(post, "id", None),
                       artifact_id=getattr(artifact, "id", None), filename=path.name,
                       mime_type=MIME.get(image["ext"], "application/octet-stream"),
                       size=len(raw), data=raw))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

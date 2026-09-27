"""The git state of one ticket: repository, ticket branch, worktree.

Shared by the worker (before an agent run) and the CLI session delivery (before a ticket is
typed into a person's Claude session): both need the same branch in the same place, because
acceptance merges `issue.branch_name` no matter who wrote the commits on it.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.project import Project
from ..models.ticket import Issue
from . import gitops
from .secrets import resolve_git_token

log = logging.getLogger("traccoon.worker")


async def prepare_issue_git(db: AsyncSession, issue: Issue, project: Project,
                            owner_id: int | None) -> gitops.GitCtx | None:
    """Set up the ticket's branch and worktree and record them on the ticket (committed).

    None when the project has no git. A sub-ticket branches off the branch of its umbrella
    ticket and merges back there.
    """
    if not project.git_enabled:
        return None
    host = urlsplit(project.github_repo).hostname or ""
    token = await resolve_git_token(db, project.git_token_enc, owner_id, host) or ""
    wt = gitops.worktree_path(project.key, issue.key) if project.work_in_branches else None
    base_branch = project.merge_target or "main"
    if issue.parent_ticket_id:
        umbrella = await db.get(Issue, issue.parent_ticket_id)
        if umbrella:
            umb_branch = umbrella.branch_name or gitops.issue_branch(umbrella.key)
            ens = gitops.GitCtx(
                workdir=gitops.project_workdir(project.key), branch=umb_branch,
                remote=project.github_repo, token=token,
                main=project.merge_target or "main", enabled=True)
            log.info("git ensure-umbrella %s: %s", umbrella.key,
                     await gitops.ensure_branch(ens, umb_branch, project.merge_target or "main"))
            if not umbrella.branch_name:
                umbrella.branch_name = umb_branch
                umbrella.base_branch = project.merge_target or "main"
                await db.commit()
            base_branch = umb_branch
    ctx = gitops.GitCtx(
        workdir=gitops.project_workdir(project.key), branch=gitops.issue_branch(issue.key),
        remote=project.github_repo, token=token, worktree=wt, main=base_branch,
        enabled=True)
    note = await gitops.prepare(ctx)
    log.info("git prepare %s: %s", issue.key, note)
    issue.branch_name = ctx.branch
    issue.base_branch = ctx.main
    issue.git_base_sha = ctx.base_commit
    await db.commit()
    return ctx

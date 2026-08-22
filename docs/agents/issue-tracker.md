# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

Repository: `janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration`.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open`
- Comment: `gh issue comment <number> --body "..."`
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares one number space across issues and pull requests. Resolve an
ambiguous reference such as `#42` with `gh pr view 42`, falling back to
`gh issue view 42`.

## Skill operations

When a skill says “publish to the issue tracker”, create a GitHub issue.

When a skill says “fetch the relevant ticket”, run:

`gh issue view <number> --comments`

## Wayfinding operations

A wayfinding map is an issue labelled `wayfinder:map`. Its child tickets use
`wayfinder:<type>`, where the type is `research`, `prototype`, `grilling`, or
`task`.

Use GitHub sub-issues and native issue dependencies when available. Otherwise,
use task lists and `Blocked by: #<number>` references.

Claim a ticket with `gh issue edit <number> --add-assignee @me`. Resolve it by
adding the result as a comment and closing the issue.

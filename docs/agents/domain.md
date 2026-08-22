# Domain Docs

This repository uses a single-context domain-documentation layout.

## Before exploring

Read these files when they exist:

- `CONTEXT.md` at the repository root
- Relevant ADRs under `docs/adr/`

If they do not exist, proceed silently. Domain-modeling skills create them
lazily when terminology or architectural decisions are actually resolved.

## Use the glossary vocabulary

Use domain terms as defined in `CONTEXT.md` in issue titles, proposals, tests,
and implementation work. Avoid synonyms that the glossary explicitly rejects.

If a required concept is missing, reconsider whether it belongs to the domain
or note it for domain modeling.

## Flag ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict explicitly
instead of silently overriding the recorded decision.

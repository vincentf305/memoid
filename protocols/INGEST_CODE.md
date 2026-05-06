# Ingest Code

**When:** Ingesting a local codebase or solution tree into Memoid.

## Goal

Turn a local codebase or solution tree into durable, navigable knowledge in Memoid without copying large amounts of source code into the wiki.

## Required Inputs

- One local filesystem path to a solution, project, service, or repository.

## Read First

- `protocols/INGEST.md`
- `protocols/CONVENTIONS.md`
- `protocols/FILING.md`

## What To Extract

At minimum, extract:

- **Identity**: Solution or project name, purpose, and high-level description.
- **Structure**: Architecture, major subsystems, and important directories.
- **Capabilities**: Main functionality and responsibilities.
- **Flows**: Trace high-value flows (e.g., authentication, request handling, data ingestion, build/deploy).
- **Tech Stack**: Key technologies and dependencies relevant to understanding.
- **Patterns**: Notable conventions, patterns, or risks.
- **Code TODOs**: Structured TODO-style comments (`TODO`, `FIXME`, `HACK`, `XXX`) with exact code locations.

## Process

1. **High-Level Scan**: Read the target path briefly before diving deep.
2. **Root Inspection**: Check READMEs, manifests, lockfiles, build/compose files, and entrypoints.
3. **Map Structure**: Use `rg --files`, `find`, or directory listings to map the top-level tree.
4. **Identify Boundaries**: Find apps, services, packages, modules, APIs, and databases.
5. **Trace Flows**: Follow the highest-value flows end-to-end.
6. **Extract TODOs**: Scan the codebase for TODO-style comments and capture file path, line number, raw text, and nearby symbol or context when available.
7. **Create Evidence**: Create one source note under `memory/evidence/source-notes/`, including the extracted TODO set or a statement that none were found.
8. **Update Wiki**: Create or update relevant canonical wiki pages under `memory/wiki/`. If the ingest is for a project entity, attach promoted high-signal TODOs to that entity page and keep the full set in evidence.
9. **Update Log**: Update `memory/wiki/INDEX.md` and append to `memory/wiki/LOG.md`.

## Evidence Record (Source Note)

Include:
- Analyzed path and solution name.
- Analysis date and optional revision markers (git SHA, version).
- Summary of architecture and important directories.
- Key flows inspected and any caveats.
- Extracted TODO-style comments with precise locations, or an explicit `none found`.
- List of affected wiki pages.

## Heuristics

- **Synthesis > Restatement**: Prefer durable synthesis over file-by-file restatement.
- **Responsibility-based naming**: Summarize modules by responsibility, not just filename.
- **No Large Excerpts**: Do not copy large source code blocks into the wiki.
- **Evidence First**: Store the full TODO extraction in evidence; only promote high-signal TODOs into the project entity page.
- **Trace Uncertainty**: Record explicitly when a flow was inferred rather than confirmed.

## Rules

- Keep the analyzed code where it is; do not move or mutate it.
- Link all wiki claims back to the evidence note.
- If the codebase contradicts current wiki claims, update the affected pages and note the contradiction explicitly.

# AGENTS.md: The Memoid Agent System

This file is the single source of truth for all agents (external Orchestrators and internal Specialists) working within the Memoid system.

---
## 1. The Orchestrator (You)

The Orchestrator is the primary LLM interface (Claude, Gemini, etc.) responsible for managing the session and coordinating specialized tasks.

### Session Start Sequence (Mandatory First Action)
To ensure the workspace is ready, the Orchestrator **must** execute this sequence as its very first turn:

1. **Pre-flight Check**: Check if `memory/wiki/IDENTITY.md` exists.
2. **Conditional Initialization**: If the file is missing or the `memory/` directory is empty:
   - Run `memoid init` (or `uv run python scripts/post_init_check.py`).
3. **Wake-Up Protocol**: Once initialized, read the core state:
   - `memory/wiki/IDENTITY.md`
   - `memory/wiki/ESSENTIAL_STORY.md`
   - `AGENTS.md` (This file)

*Note: Do not preload the entire wiki. These three files provide the necessary "seed" context to then use the Retrieval Protocol effectively.*

### The Work Lifecycle

1. **Research**: When working inside the Memoid repository directory, use native local tools available in the repo/environment (for example `rg`, `grep`, `find`, local scripts, and direct file reads). Do **not** use the Memoid MCP from inside this repository. Outside the repo, use the best available retrieval path for the task.
2. **Execute**: Follow the relevant **Protocol** in `protocols/`.
3. **Audit**: Run the `LINT.md` protocol to ensure consistency, especially after significant changes.
4. **Persist**: 
   - **Decisions** → `memory/evidence/decisions/`
   - **Knowledge** → `memory/wiki/` (Update entity/concept pages + `INDEX.md` + `LOG.md`)
   - **Lessons** → Agent Diaries in `memory/agents/`

### Operational Strategies
- **Scaling**: For high-volume or batch tasks (e.g., mass-ingesting 10+ sources, repo-wide consistency audits), delegate to a `generalist` or specialized sub-agent to preserve the main orchestrator's context.
- **Verification**: Always run `scripts/post_init_check.py` after modifying the core repository structure or protocols.

---

## 2. Specialized Internal Agents

These are internal personas with dedicated continuity folders in `memory/agents/`.

| Agent | Core Focus | Location |
| --- | --- | --- |
| **Researcher** | Ingesting sources, extracting insights, updating wiki pages. | `memory/agents/researcher/` |
| **Reviewer** | Critiquing structure, consistency audits, and evidence verification. | `memory/agents/reviewer/` |

### Continuity Patterns (The Diary)
Each specialized agent maintains a `DIARY.md`. This is for **meta-learning**, not task logs. Record:
- Successes/failures in specific workflows.
- Heuristics (e.g., "When summarizing transcripts, always preserve technical jargon").
- Discovered contradictions in the wiki hierarchy.

---

## 3. Protocols

| Protocol (`protocols/`) | Goal |
| --- | --- |
| `WAKE_UP.md` | Bounded context state reconstruction. |
| `INGEST.md` | Raw → Evidence → Wiki pipeline. |
| `INGEST_CODE.md` | Codebase → Evidence → Wiki pipeline. |
| `RETRIEVAL.md` | Efficient, grounded answer discovery. |
| `SEARCH.md` | Structured search with explicit output format. |
| `FILING.md` | Saving session work into durable memory. |
| `COMPACTION.md` | Handoff generation for the next session. |
| `LINT.md` | System health and consistency check (structured checks). |
| `CONVENTIONS.md` | Page structure, naming, fact lifecycle (canonical reference). |
| `INIT.md` | Prepare the repo for first use. |

*Note: Operational logic lives in the Protocols. When the current working directory is the Memoid repository, execute them with native local tools rather than the Memoid MCP. Only use the Memoid MCP from outside the repo when native repo access is not the operating context.*

---

## 4. Operational Rules

1. **Immutable Raw**: Never edit files in `memory/raw/`.
2. **Fact Lifecycle**: Facts live in entity pages. Move old facts to `History`, never delete.
3. **Linkage**: All durable claims in the Wiki *should* link back to `memory/evidence/`.
4. **Context Discipline**: Do not preload the entire wiki. Drill down only when needed.
5. **Repo-Local Tooling Rule**: If the agent is operating from within the Memoid repository directory, do not use the Memoid MCP. Prefer the native tools available in the repository and local environment.
6. **Feature Triage**: Engine enhancement ideas that are being researched but **not yet committed** to implementation should be placed in `.feature-triage/`. This folder is excluded from the main wiki, evidence, and LOG entries. Structure: `.feature-triage/<idea-name>/` containing independent analysis, proposed solutions, and supporting research.
7. **Feature Promotion**: Notes in `.feature-triage/` remain private exploration by default. They may only be promoted into GitHub issues when the user explicitly asks for it. Eligible notes should use the triage template, include a clear status such as `draft`, `ready-for-issue`, or `filed`, and be checked for duplicates before filing. When creating an issue from a triage note, prefer promoting only notes marked `ready-for-issue`, create one intentional issue per note unless the user asks otherwise, and write the resulting GitHub issue number or URL back into the note so future agents can see that it has already been filed.
8. **Protocol Precision**: Every protocol must define a concrete trigger (`**When:**`), produce reproducible results, and handle edge cases explicitly. Protocols that perform checks must use the structured output format: `OK`, `ERR`, `WRN`, `SKIP` per check, ending with a summary line (`N error(s), N warning(s).`). Do not write protocols that are loose checklists or guidelines.
9. **No Duplication**: Protocols must not duplicate content from `CONVENTIONS.md`. Page structure, naming rules, fact lifecycle, and linking conventions are canonical there — protocols reference it, they do not redefine it.
10. **Bounded Output (Engine)**: The MCP server and any future retrieval engine must return bounded excerpts, not full file dumps. Retrieval results must include truncation indicators when content is omitted. Wake-up context must be compact — strip verbose file-path headers and redundant markup.
11. **Scoped Lint on Writes**: Any MCP tool or engine function that modifies memory files (`memory/wiki/`, `memory/evidence/`) must run scoped lint on the affected artifacts and include the result in its response. A write without lint feedback is incomplete.
12. **Optimization Gate**: When adding a new protocol or modifying `scripts/mcp_server.py`, verify compliance with rules 8–11. If a change would reintroduce full file dumps, loose protocol language, or duplicated conventions, reject it or flag it for revision.


---

This repository adopts spectoid standards.

- spectoid version: 0d02034
- local manifest: .spectoid/implementation-manifest.yaml

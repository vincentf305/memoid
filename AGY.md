# AGY.md

Memoid Orchestration Guide for Antigravity CLI (agy).

## 0. Foundational Mandates
**CRITICAL**: At the start of every new session, your **absolute first action** must be to execute the **Session Start Sequence** defined in `AGENTS.md`. 
1. **Verify** initialization (Check if `memory/wiki/IDENTITY.md` exists).
2. **Initialize** if missing (`uv sync` && `post_init_check.py`).
3. **Wake-Up** (Read Identity and Essential Story).

You must complete this sequence before answering any user query or performing any other task.

## 1. Primary Source of Truth
**Read `AGENTS.md`** for the core system architecture, wake-up protocols, and agent roles.

## 2. Key Protocols
- **Structure & Conventions**: `protocols/CONVENTIONS.md` — page types, naming, fact lifecycle, editing rules
- **Ingest**: `protocols/INGEST.md` and `protocols/INGEST_CODE.md`
- **Retrieval**: `protocols/RETRIEVAL.md` and `protocols/SEARCH.md`
- **Maintenance**: `protocols/LINT.md` and `protocols/FILING.md`

## 3. Quick Commands
- **Init**: `uv sync && uv run python scripts/post_init_check.py`
- **Ingest**: `uv run python skills/download-urls/scripts/download_urls.py <url>`

## 4. Antigravity CLI (agy) Specific Guidance
- **Discovery**: Use `grep_search`, local directory reads, and scripts to find files when operating natively inside the repository. Use the MCP server tools like `memoid_recall` when operating remotely.
- **Planning**: Use `enter_plan_mode` (or standard `planning_mode` flow) for significant structural changes to the Wiki or Protocols.
- **Scaling & Subagents**: Leverage the specialized background subagents (like `researcher` or `reviewer`) for heavy batch operations or parallel tasks to preserve the main orchestrator's context.
- **Validation**: Always run `scripts/post_init_check.py` and run a Memoid lint check using `protocols/LINT.md` after modifying files.

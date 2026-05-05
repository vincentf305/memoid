# Wake-Up

**When:** Starting a new session or the user asks to "wake up" or orient.

## Goal

Reconstruct current state from a very small default context.

## Required Reads

1. `memory/wiki/IDENTITY.md`
2. `memory/wiki/ESSENTIAL_STORY.md`

## Optional Reads

Read only if needed:

- `memory/wiki/INDEX.md`
- one relevant domain page
- one relevant agent diary
- `memory/wiki/PERSONA.md` if it exists

## Rules

- Do not preload the entire repo.
- Default to bounded context.
- If `memory/wiki/PERSONA.md` exists, load it during wake-up and treat it as the active persona/style overlay for personality, output style, and formatting.
- If `memory/wiki/PERSONA.md` does not exist, continue without error.
- Use retrieval when a question needs more than the wake-up files contain.

## Output of Wake-Up

The agent should be able to state:

- what this system is for
- what areas are currently active
- what important unresolved threads exist
- what to read next if the task is domain-specific
- what persona/style instructions are active, if `memory/wiki/PERSONA.md` was loaded

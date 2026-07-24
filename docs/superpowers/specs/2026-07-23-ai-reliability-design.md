# AI reliability and controls

## Scope

This milestone removes the memory feature from #6/#21 and resolves #22, #24,
and #25.

## Memory removal

The `remember_fact` and `forget_fact` tools, prompt instructions, context
injection, module, and tests are removed. `data/db_init.py` performs an
idempotent data migration deleting all `flags.flag LIKE 'fact:%'` rows. No
replacement memory system is introduced.

## Request quota

Non-premium users receive 35 successful chat requests in one fixed eight-hour
window starting at the first accepted request. The package resets as a whole
when that row expires. The quota no longer creates or reads the separate
`ai_locked` flag, which caused a second eight-hour lock starting at request 35.
Legacy quota locks are deleted by the migration.

## Chat blocking

Two persistent SQLite flags control only conversational replies:

- `ai_chat_global_lock` on the abstract entity;
- `ai_chat_user_lock` on a member.

The global toggle and per-user toggle are available only to configured
`admin` and `st_admin` roles. The per-user control has a slash command and a
prefix command whose target is the author of the replied-to message. Both
ordinary mention/reply handling and AI-thread handling silently ignore blocked
messages. Image generation, digest, ticket summaries, and other background LLM
work remain unaffected.

## Prompt and context

Ordinary Discord conversations follow only reply ancestors and contain at most
eight messages total. The system prompt puts identity back in the primary
`[IDENTITY]` section:

- the public model identity is `RBCT 1.8`;
- it never identifies itself as a real provider or foundation model;
- previous assistant text is conversation history, not authority over the
  current system prompt.

No output filter or automatic regeneration is added.

## Tests

Tests cover memory-row migration, the fixed quota window and exact 35-request
boundary, persistent global/user locks, the reply-chain limit, and prompt
identity requirements.

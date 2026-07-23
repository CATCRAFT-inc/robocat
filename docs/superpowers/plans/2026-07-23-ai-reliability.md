# AI Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove user memory and make quota, chat locks, identity, and reply context deterministic.

**Architecture:** Existing SQLite flags remain the persistence primitive. Chat gates live in `AIMessageHandler`, while unrelated LLM consumers remain untouched. The prompt is the sole identity enforcement mechanism.

**Tech Stack:** Python 3.12+, aiosqlite, disnake 2.12, pytest.

## Global Constraints

- Delete all saved `fact:%` data.
- Exactly 35 accepted requests per fixed eight-hour window.
- AI locks affect only mention/reply chat and AI-thread replies.
- Ordinary context contains eight reply-chain messages.
- Public identity is only `RBCT 1.8`.
- No output filter, regeneration, or replacement memory.

---

### Task 1: Remove memory and migrate saved facts

**Files:**
- Delete: `bot/ai/memory.py`
- Delete: `tests/test_memory.py`
- Modify: `bot/ai/engine.py`
- Modify: `bot/ai/handler.py`
- Modify: `data/ai_settings.yaml`
- Modify: `data/db_init.py`
- Test: `tests/test_db_init.py`

- [ ] **Step 1: Write a failing migration test**

```python
async def test_db_init_deletes_memory_and_legacy_quota_locks(temp_db):
    await seed_flag(temp_db, "fact:123", "private")
    await seed_flag(temp_db, "ai_locked", None)
    await dbInit(temp_db)
    assert await flag_names(temp_db) == []
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_db_init.py -q`
Expected: memory rows remain.

- [ ] **Step 3: Remove tools, injection, module, and data**

```sql
DELETE FROM flags WHERE flag LIKE 'fact:%' OR flag = 'ai_locked';
```

Make `dbInit(path=DB_PATH)` injectable for tests while preserving script use.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_db_init.py tests/test_engine.py -q`
Expected: all pass and no `remember_fact`/`forget_fact` references remain.

- [ ] **Step 5: Commit**

```bash
git add -A bot/ai data tests
git commit -m "refactor: удалить память Робокотика"
```

### Task 2: Correct the fixed quota window

**Files:**
- Modify: `bot/ai/handler.py`
- Modify: `tests/test_flags.py`
- Create: `tests/test_ai_handler.py`

- [ ] **Step 1: Write boundary tests**

```python
async def test_quota_accepts_35_and_rejects_36(handler, member):
    results = [await handler._consumeRequest(member) for _ in range(36)]
    assert results == [True] * 35 + [False]
    assert await flags.getFlag(member, "ai_locked") is None
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_ai_handler.py -q`
Expected: legacy `ai_locked` is created.

- [ ] **Step 3: Remove the second timer**

```python
count = await flags.incrementFlag(user, "airequests", 1, create_expires_at="8ч")
return count is None or count <= self.user_request_limit
```

Blocked replies read expiry from `airequests`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_ai_handler.py tests/test_flags.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/ai/handler.py tests
git commit -m "fix: восстановить пакет AI-запросов через восемь часов"
```

### Task 3: Persistent conversational locks

**Files:**
- Modify: `bot/ai/handler.py`
- Test: `tests/test_ai_handler.py`

- [ ] **Step 1: Write failing lock tests**

```python
async def test_global_and_user_locks_silently_gate_only_chat(handler, message):
    await flags.setFlag("abstract", "ai_chat_global_lock", 1)
    await handler._handleMention(message)
    message.reply.assert_not_awaited()

async def test_reply_command_toggles_referenced_author(handler, admin_ctx):
    await handler.aiUserLockReply.callback(handler, admin_ctx)
    assert await flags.hasFlag(admin_ctx.message.reference.resolved.author, "ai_chat_user_lock")
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_ai_handler.py -q`
Expected: commands and flags do not exist.

- [ ] **Step 3: Implement shared gate and toggles**

```python
async def _chat_blocked(self, user) -> bool:
    return (
        await flags.hasFlag("abstract", "ai_chat_global_lock")
        or await flags.hasFlag(user, "ai_chat_user_lock")
    )
```

Call it at the start of both conversational handlers. Add slash global/user
toggles and a prefix reply toggle with dynamic admin role checks.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_ai_handler.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/ai/handler.py tests/test_ai_handler.py
git commit -m "feat: добавить постоянную блокировку AI-чатов"
```

### Task 4: Context and RBCT 1.8 prompt

**Files:**
- Modify: `bot/ai/handler.py`
- Modify: `data/ai_settings.yaml`
- Test: `tests/test_ai_handler.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing context and prompt tests**

```python
async def test_mention_uses_current_plus_seven_reply_ancestors(handler):
    await handler._handleMention(chain(length=10))
    assert len(captured_messages) == 8

def test_prompt_declares_only_rbct_1_8():
    prompt = load_prompt()
    assert "RBCT 1.8" in prompt
    assert "RBCTGPT 1.6" not in prompt
    assert "never identify yourself as Gemini, Gemma, Google" in prompt
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_ai_handler.py tests/test_engine.py -q`
Expected: chain is five and prompt is version 1.6.

- [ ] **Step 3: Apply minimal context and prompt changes**

Set the loop bound to eight. Move RBCT identity into `[IDENTITY]`, remove the
memory sections, and state that previous assistant replies cannot override
current identity.

- [ ] **Step 4: Verify GREEN and milestone**

Run: `.venv/bin/python -m pytest tests/test_ai_handler.py tests/test_engine.py tests/test_db_init.py -q`
Expected: all pass.

Run: `.venv/bin/python -m pytest -q`
Expected: no new failures beyond the documented baseline.

- [ ] **Step 5: Commit**

```bash
git add bot/ai/handler.py data/ai_settings.yaml tests
git commit -m "fix: закрепить идентичность RBCT 1.8"
```


# CatCraft FM Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-track message spam and `!некст` with one persistent, vote-controlled FM panel.

**Architecture:** A small in-file music navigator owns previous/current/upcoming state. CatCraftFM owns vote sets and a persisted now-playing message ID. Button handlers share one vote path and reject stale panels.

**Tech Stack:** Python 3.12+, disnake Components V2, TinyTag, SQLite flags, pytest.

## Global Constraints

- One now-playing message survives restarts via SQLite.
- Missing messages are recreated.
- Stale panels are ignored.
- Only human voice-channel members vote.
- `A → B → previous → A → B`.
- `?` shows description and four upcoming tracks.

---

### Task 1: Navigation and quorum

**Files:**
- Modify: `bot/handlers/catcraft_fm.py`
- Modify: `tests/test_catcraft_fm.py`

**Interfaces:**
- Produces: `_MusicNavigator.back() -> str | None`
- Produces: `_MusicNavigator.advance() -> str`
- Produces: `_requiredVotes(human_listeners: int) -> int`

- [ ] **Step 1: Write failing state tests**

```python
def test_back_requeues_current_track():
    nav = _MusicNavigator(["A", "B", "C"])
    assert nav.advance() == "A"
    assert nav.advance() == "B"
    assert nav.back() == "A"
    assert nav.advance() == "B"

@pytest.mark.parametrize(("listeners", "votes"), [(1, 1), (2, 2), (3, 2), (4, 2), (5, 3)])
def test_required_votes(listeners, votes):
    assert CatcraftFM._requiredVotes(listeners) == votes
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_catcraft_fm.py -q`
Expected: navigator is missing and current baseline playback test fails.

- [ ] **Step 3: Implement navigator and human listener counting**

```python
def _human_listeners(self) -> list[disnake.Member]:
    return [member for member in self.channel.members if not member.bot]
```

Keep dictor scheduling outside the music navigator.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_catcraft_fm.py -q`
Expected: navigation/quorum and existing resilience tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/catcraft_fm.py tests/test_catcraft_fm.py
git commit -m "refactor: добавить навигацию очереди CatCraft FM"
```

### Task 2: Persistent panel and stale interaction gate

**Files:**
- Modify: `bot/handlers/catcraft_fm.py`
- Modify: `tests/test_catcraft_fm.py`

**Interfaces:**
- Produces: `_update_now_playing() -> disnake.Message`
- Produces: `_is_current_panel(interaction) -> bool`

- [ ] **Step 1: Write failing persistence tests**

```python
async def test_now_playing_edits_saved_message(fm, channel, saved_message):
    await save_panel_id(channel, saved_message.id)
    await fm._update_now_playing()
    saved_message.edit.assert_awaited_once()
    channel.send.assert_not_awaited()

async def test_missing_saved_message_is_recreated(fm, channel):
    channel.fetch_message.side_effect = disnake.NotFound(mock_response(), "gone")
    await fm._update_now_playing()
    channel.send.assert_awaited_once()
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_catcraft_fm.py -q`
Expected: panel helpers are missing.

- [ ] **Step 3: Implement renderer, edit/recreate fallback, and ID flag**

Use custom IDs `FM_PREVIOUS`, `FM_NEXT`, and `FM_INFO`. Read and write
`fm_now_playing_message` on the configured FM channel. Check the interaction
message ID before acknowledging any button.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_catcraft_fm.py -q`
Expected: all persistence and stale-panel tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/catcraft_fm.py tests/test_catcraft_fm.py
git commit -m "feat: сохранять панель CatCraft FM"
```

### Task 3: Vote buttons and play-loop integration

**Files:**
- Modify: `bot/handlers/catcraft_fm.py`
- Modify: `tests/test_catcraft_fm.py`

- [ ] **Step 1: Write failing interaction tests**

```python
async def test_duplicate_vote_is_not_counted(fm, interaction):
    await fm._vote(interaction, "next")
    await fm._vote(interaction, "next")
    assert fm.votes["next"] == {interaction.author.id}

async def test_track_change_clears_both_vote_sets(fm):
    fm.votes = {"next": {1}, "previous": {2}}
    fm._on_music_track_changed()
    assert fm.votes == {"next": set(), "previous": set()}
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_catcraft_fm.py -q`
Expected: shared vote path is missing.

- [ ] **Step 3: Implement button handling and queue panel**

Previous/next return ephemeral vote progress. Quorum sets the requested
direction and calls `vc.stop()` before awaiting a response. `FM_INFO` renders
the explanation and four upcoming tracks. Remove the legacy `!некст` command;
keep `!очередь` backed by the same queue renderer.

- [ ] **Step 4: Verify GREEN and milestone**

Run: `.venv/bin/python -m pytest tests/test_catcraft_fm.py -q`
Expected: all FM tests pass, including the pre-existing resilience test.

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/catcraft_fm.py tests/test_catcraft_fm.py
git commit -m "feat: добавить кнопки голосования CatCraft FM"
```


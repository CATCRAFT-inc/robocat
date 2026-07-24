# CatCraft FM persistent controls

## Scope

This milestone resolves #26.

## Now-playing message

CatCraft FM maintains one Components V2 now-playing message. Its message ID is
stored in SQLite on the FM voice channel. Each music-track change fetches and
edits that message. If the flag is absent or the message was deleted or became
inaccessible, the bot creates a replacement and stores its ID.

Button interactions are accepted only from the currently stored message ID.
Buttons on superseded messages are silently ignored.

## Queue and navigation

Music navigation keeps three pieces of state: previous music tracks, the
current music track, and upcoming music tracks. Going back from B to A places B
at the front of upcoming tracks, so the full sequence becomes
`A → B → A → B`.
Dictor inserts are not exposed as music-history entries.

Every track change clears both vote sets. The previous button is disabled when
history is empty.

## Voting

Previous and next have independent voter sets but share one quorum rule:

- one human listener requires one vote;
- two human listeners require two votes;
- three or more require half, rounded up.

All bot accounts are excluded. A person can vote once per direction per track,
and must currently be in the FM voice channel. Progress and errors are
ephemeral. Reaching quorum stops the current voice source and schedules the
selected direction.

## Information

The `?` button returns an ephemeral Components V2 panel with a short CatCraft
FM explanation and the next four music tracks. The legacy queue command may
reuse the same renderer; `!некст` is superseded by the button.

## Tests

Tests cover human-only quorum, duplicate votes, vote reset, `A → B → A → B`
navigation, persistent message edit/recreate, stale-button ignoring, and the
four-track information panel.

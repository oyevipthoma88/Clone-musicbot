"""
🎞️ Custom (premium) emoji resolution — single source of truth.

WHY THIS EXISTS
----------------
Every premium-emoji code path used to call
``client.get_custom_emoji_stickers(ids)`` and then read
``sticker.custom_emoji_id``. On pyrofork (TL layer 220+) the returned
``Sticker`` object does **not** expose ``custom_emoji_id`` at all, so every
call blew up with::

    'Sticker' object has no attribute 'custom_emoji_id'

which the callers caught and treated as "no id resolved" — i.e. premium
emoji were silently degraded to plain glyphs everywhere, and the startup
map rebuild logged "Premium emoji map resolution failed".

The fix: go one layer lower and ask Telegram for the raw documents
(``messages.GetCustomEmojiDocuments``). A raw ``Document`` always carries
its own ``id`` (that *is* the custom_emoji_id) plus a
``DocumentAttributeCustomEmoji`` holding the ``alt`` glyph — exactly the two
things the callers need, with no dependency on high-level wrapper fields.
"""

from typing import Dict, Iterable, Optional

import logging
import time
import asyncio

from pyrogram import raw

log = logging.getLogger(__name__)

# Telegram caps messages.GetCustomEmojiDocuments at 200 document ids.
MAX_IDS_PER_REQUEST = 200


class EmojiResolutionUnavailable(RuntimeError):
    """Telegram could not be asked whether an id is valid *at all*.

    ROOT-CAUSE FIX ("bot start karne pe bohot saare normal emoji aate hai"):
    ``resolve_custom_emoji`` swallowed every RPC failure and returned an empty
    mapping, which callers could not tell apart from "Telegram says none of
    these ids exist". ``verify_custom_emojis`` therefore unwrapped EVERY
    <emoji> tag — the whole card rendered with plain unicode emoji. Bot
    accounts cannot even call ``messages.GetCustomEmojiDocuments``
    (BOT_METHOD_INVALID), so this happened on every single send.

    Callers must treat this as "cannot verify" and keep the ids (fail open),
    never as "ids are invalid".
    """


# Set to False once Telegram tells us this account may not call the method at
# all, so we stop paying for a round-trip that can never succeed.
RESOLUTION_SUPPORTED = True
# A transient Telegram timeout should not trigger one RPC per queued glyph.
# Premium-emoji verification is a nonessential presentation feature, so back
# off briefly and fail open while playback continues normally.
_RESOLUTION_RETRY_AT = 0.0
_RESOLUTION_RETRY_COOLDOWN = 60.0
_RESOLUTION_LOCK = asyncio.Lock()

_UNSUPPORTED_MARKERS = ("BOT_METHOD_INVALID", "METHOD_INVALID", "USER_BOT_INVALID")


async def _resolve_custom_emoji_unlocked(client, ids: Iterable[int]) -> Dict[int, Optional[str]]:
    """Return ``{custom_emoji_id: alt_glyph_or_None}`` for every id that
    Telegram can actually resolve right now. Unresolvable ids are simply
    absent from the mapping. Never raises — failures are logged and yield an
    empty/partial mapping so a bad id can never break a send.

    Raises ``EmojiResolutionUnavailable`` when *no* batch could be asked at
    all (network error, or a bot account that may not call the method) — that
    is "unknown", not "invalid".
    """
    global RESOLUTION_SUPPORTED, _RESOLUTION_RETRY_AT
    wanted = [int(i) for i in dict.fromkeys(ids)]
    resolved: Dict[int, Optional[str]] = {}

    if not wanted:
        return resolved
    if not RESOLUTION_SUPPORTED:
        raise EmojiResolutionUnavailable(
            "messages.GetCustomEmojiDocuments is not available for this account"
        )
    if time.monotonic() < _RESOLUTION_RETRY_AT:
        raise EmojiResolutionUnavailable(
            "custom-emoji verification is temporarily backing off after a timeout"
        )

    failures = 0
    for start in range(0, len(wanted), MAX_IDS_PER_REQUEST):
        batch = wanted[start:start + MAX_IDS_PER_REQUEST]
        try:
            documents = await client.invoke(
                raw.functions.messages.GetCustomEmojiDocuments(document_id=batch)
            )
        except Exception as exc:  # noqa: BLE001 - never let this kill a send
            failures += 1
            _RESOLUTION_RETRY_AT = time.monotonic() + _RESOLUTION_RETRY_COOLDOWN
            if any(m in str(exc).upper() for m in _UNSUPPORTED_MARKERS):
                RESOLUTION_SUPPORTED = False
                log.info(
                    "Custom-emoji id verification unavailable for this account "
                    "(%s) — premium emoji ids will be used unverified.", exc,
                )
            else:
                log.warning(
                    "Custom-emoji verification temporarily unavailable (%s); "
                    "backing off for %.0fs.", exc, _RESOLUTION_RETRY_COOLDOWN,
                )
            continue

        for document in documents or []:
            document_id = getattr(document, "id", None)
            if document_id is None:
                continue
            alt = None
            for attribute in getattr(document, "attributes", None) or []:
                if isinstance(attribute, raw.types.DocumentAttributeCustomEmoji):
                    alt = attribute.alt
                    break
            resolved[int(document_id)] = alt

    if failures and not resolved:
        raise EmojiResolutionUnavailable(
            f"could not verify {len(wanted)} custom-emoji id(s)"
        )

    return resolved


async def resolve_custom_emoji(client, ids: Iterable[int]) -> Dict[int, Optional[str]]:
    """Resolve one batch at a time across all concurrent message sends.

    During a Telegram RPC outage many cards can enter verification together.
    Without a process-wide lock every caller starts its own ten-attempt
    Pyrogram retry sequence before the cooldown becomes visible to the others,
    producing a large retry storm and wasting the event loop on presentation
    work that is explicitly best-effort.
    """
    async with _RESOLUTION_LOCK:
        return await _resolve_custom_emoji_unlocked(client, ids)

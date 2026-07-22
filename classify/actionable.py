"""Decide whether a Slack message is something Craig has to act on.

Ported from the Roland Express server (`fetchSlackItems` in `server.js`). A
search for `to:me` returns everything addressed to Craig, the large majority of
which is acknowledgements, scheduling noise, and social chatter. Without this
filter the task list is unusable.

Negative signals come in two strengths, which matters because action verbs are
ambiguous in English:

* **Hard** — availability updates, acknowledgements, bare time replies, "I'll
  get back to you". These are never asks. Only a question mark or a bullet list
  overrides them. An action verb does not: "won't be on the call" contains
  "call", and the Node original's flat rule let exactly that through.
* **Soft** — social chatter ("congrats", "feel better"). An action verb *does*
  override these, so "congrats on the launch, can you send the deck" survives.
  The Node original dropped it, because one social word vetoed the whole message.
"""
from __future__ import annotations

import re

# Slack user mention: <@U123> or <@U123|alice>
_USER_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|([^>]+))?>")
# Channel ref: <#C123> or <#C123|general>
_CHANNEL_REF = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]+))?>")
# Link: <https://x.com|label> or <https://x.com>. Keep the label when present —
# dropping it turns "check <url|the SOW>?" into a meaningless "check ?".
_LINK = re.compile(r"<([^|>]+)\|([^>]+)>")
# Anything still bracketed after the above (special tokens like <!here>).
_OTHER_MARKUP = re.compile(r"<[^>]+>")

# Bare-label lines that carry no information on their own.
_SKIP_LINE = re.compile(
    r"^(critical|important|urgent|fyi|note|update|hi|hey|hello|thanks|thank you)[\s:!.]*$",
    re.IGNORECASE,
)

_ACTION_VERB = re.compile(
    r"\b(can you|could you|please|review|send|forward|follow[- ]?up|schedule|book|"
    r"call|need|update|confirm|approve|check|share|provide|submit|complete|sign|"
    r"respond|reply|attach|upload|download|let me know|lmk|asap)\b",
    re.IGNORECASE,
)

# A bullet or numbered list usually means a handoff of several items.
_TASK_LIST = re.compile(r"(\n[-*•]|\d+\.)")

# Availability / status updates — informational, never an ask.
_STATUS_UPDATE = re.compile(
    r"\b(stomach|sick|ill|out of office|ooo|wfh|working from home|won'?t be|"
    r"will not be|can'?t make|cannot make|not able to|running late|be late|"
    r"on my way)\b",
    re.IGNORECASE,
)

# Whole message is an acknowledgement.
_ACKNOWLEDGEMENT = re.compile(
    r"^(sounds good|great|perfect|got it|ok|okay|sure|noted|yes|no|thanks|"
    r"thank you|thx|ty|np|no problem|will do|done|on it|10-4|roger|copy|"
    r"understood)[.!,\s]*$",
    re.IGNORECASE,
)

# Bare time replies: "lets do 9", "how about 3pm".
_TIME_REPLY = re.compile(
    r"^(let'?s? do|how about|what about|maybe)?\s*\d{1,2}(:\d{2})?\s*(am|pm)?\s*[.!?]*$",
    re.IGNORECASE,
)

_DEFERRAL = re.compile(
    r"\b(just wanted to (let you know|update you|inform you|give you an update)|"
    r"i'?ll get back to you|will get back to you|getting back to you)\b",
    re.IGNORECASE,
)

_SOCIAL = re.compile(
    r"\b(feel better|get well|aww|haha|lol|congrats|congratulations)\b",
    re.IGNORECASE,
)

# Below this word count, a message needs a question/verb/list to survive.
_SHORT_MESSAGE_WORDS = 8


def clean_text(raw: str) -> str:
    """Strip Slack markup, keeping the readable parts (@names, link labels)."""
    if not raw:
        return ""

    def _named(m: re.Match, sigil: str) -> str:
        label = m.group(2)
        return f"{sigil}{label}" if label else ""

    text = _USER_MENTION.sub(lambda m: _named(m, "@"), raw)
    text = _CHANNEL_REF.sub(lambda m: _named(m, "#"), text)
    text = _LINK.sub(lambda m: m.group(2), text)
    text = _OTHER_MARKUP.sub("", text)
    # Markup removal can leave doubled spaces mid-sentence.
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def headline(cleaned: str) -> str:
    """First line that actually says something, for use as the task title."""
    for line in cleaned.split("\n"):
        line = line.strip()
        if line and not _SKIP_LINE.match(line):
            return line
    return ""


_HARD_NEGATIVES = (_STATUS_UPDATE, _ACKNOWLEDGEMENT, _TIME_REPLY, _DEFERRAL)
_SOFT_NEGATIVES = (_SOCIAL,)


def is_actionable(cleaned: str) -> bool:
    """True when the message plausibly needs a response or a task."""
    if not headline(cleaned):
        return False

    has_question = "?" in cleaned
    has_task_list = bool(_TASK_LIST.search(cleaned))
    has_action_verb = bool(_ACTION_VERB.search(cleaned))

    # Only an unambiguous signal beats a hard negative. Action verbs do not
    # qualify: "won't be on the call" matches \bcall\b but asks for nothing.
    explicit_ask = has_question or has_task_list
    if not explicit_ask and any(p.search(cleaned) for p in _HARD_NEGATIVES):
        return False

    # Social chatter is weaker evidence, so an action verb is enough to keep it.
    if not (explicit_ask or has_action_verb) and any(
        p.search(cleaned) for p in _SOFT_NEGATIVES
    ):
        return False

    if explicit_ask or has_action_verb:
        return True

    # No signal either way: keep it only if there is enough substance to judge.
    return len([w for w in cleaned.split() if w]) > _SHORT_MESSAGE_WORDS

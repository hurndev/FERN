PROTOCOL_VERSION = "fern-bft-1"

MAX_EVENT_BYTES = 32 * 1024
MAX_BLOCK_BYTES = 2 * 1024 * 1024
MAX_BLOCK_EVENTS = 500
# Proposers leave ample room for timestamp evidence and the block envelope.
# This policy bound is intentionally below the consensus validity limit.
MAX_CANDIDATE_EVENT_BYTES = MAX_BLOCK_BYTES // 2
MAX_VALIDATORS = 100
STANDARD_BFT_MIN_VALIDATORS = 4

PHASE_PREVOTE = "prevote"
PHASE_PRECOMMIT = "precommit"
VOTE_PHASES = frozenset({PHASE_PREVOTE, PHASE_PRECOMMIT})

GOVERNANCE_TYPES = frozenset(
    {
        "invite",
        "kick",
        "ban",
        "unban",
        "admin_add",
        "admin_remove",
        "validator_update",
        "metadata_update",
        "chat.channel_create",
        "chat.channel_update",
        "chat.channel_delete",
        "chat.settings_update",
    }
)

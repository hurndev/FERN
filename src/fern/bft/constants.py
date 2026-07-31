PROTOCOL_VERSION = "fern-bft-1"

MAX_EVENT_BYTES = 32 * 1024
MAX_BLOCK_BYTES = 2 * 1024 * 1024
MAX_BLOCK_EVENTS = 500
# Proposers leave ample room for timestamp evidence and the block envelope.
# This policy bound is intentionally below the consensus validity limit.
MAX_CANDIDATE_EVENT_BYTES = MAX_BLOCK_BYTES // 2
MAX_VALIDATORS = 100
STANDARD_BFT_MIN_VALIDATORS = 4
MAX_NOTICE_BYTES = 200

PHASE_PREVOTE = "prevote"
PHASE_PRECOMMIT = "precommit"
VOTE_PHASES = frozenset({PHASE_PREVOTE, PHASE_PRECOMMIT})

# Core (bare) event types the protocol core validates and applies directly.
# Self-service events are authorized by the core against membership state;
# authorized events are policy decisions delegated to the app module.
SELF_SERVICE_CORE_TYPES = frozenset({"join", "leave"})
AUTHORIZED_CORE_TYPES = frozenset(
    {"invite", "kick", "ban", "unban", "validator_update", "metadata_update"}
)
CORE_EVENT_TYPES = SELF_SERVICE_CORE_TYPES | AUTHORIZED_CORE_TYPES

# Core events that are *boundary* events: applied last in a block, at most one
# per block. These change the validity domain (who may act, what may be
# referenced, who validates). ``metadata_update`` is core but ordinary — it
# changes an attribute, not the validity domain. App modules declare their own
# boundary types; the full set is ``CORE_BOUNDARY_TYPES | app.boundary_types``.
CORE_BOUNDARY_TYPES = frozenset({"invite", "kick", "ban", "unban", "validator_update"})

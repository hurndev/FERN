from __future__ import annotations

from collections.abc import Iterable

from fern.bft.app import ApplicationError, ChainHead, execute_events, genesis_chain_head
from fern.bft.blocks import Commit, verify_commit_evidence
from fern.bft.canonical import canonical_json
from fern.events.event import Event


class ChainVerificationError(ValueError):
    pass


def verify_and_apply_commit(head: ChainHead, commit: Commit) -> ChainHead:
    block = commit.block
    candidate = block.candidate
    validator_set = head.state.validator_set
    if not verify_commit_evidence(commit, validator_set):
        raise ChainVerificationError("invalid block or commit evidence")
    if block.group != head.state.group or block.chain_id != head.state.chain_id:
        raise ChainVerificationError("commit belongs to another chain")
    if block.epoch != validator_set.epoch:
        raise ChainVerificationError("commit uses the wrong validator epoch")
    if block.height != head.height + 1:
        raise ChainVerificationError("commit height is not contiguous")
    if candidate.previous_block_hash != head.block_hash:
        raise ChainVerificationError("previous block hash mismatch")
    if candidate.previous_state_root != head.state.root:
        raise ChainVerificationError("incoming state root mismatch")
    if block.previous_history_root != head.history_root:
        raise ChainVerificationError("previous history root mismatch")
    try:
        next_state = execute_events(
            head.state,
            candidate.events,
            block.certified_times_ms,
            governance=candidate.governance,
            checkpoint_height=head.height,
            checkpoint_block_hash=head.block_hash,
            history_root=head.history_root,
            logical_bytes=head.logical_bytes,
        )
    except ApplicationError as exc:
        raise ChainVerificationError(f"invalid application transition: {exc}") from exc
    if next_state.root != block.state_root:
        raise ChainVerificationError("post-state root mismatch")
    return ChainHead(
        height=block.height,
        block_hash=block.id,
        history_root=block.history_root,
        logical_bytes=head.logical_bytes + len(canonical_json(commit.to_dict())),
        state=next_state,
    )


def verify_chain(genesis: Event, commits: Iterable[Commit]) -> ChainHead:
    head = genesis_chain_head(genesis)
    for commit in commits:
        head = verify_and_apply_commit(head, commit)
    return head


__all__ = ["ChainVerificationError", "verify_and_apply_commit", "verify_chain"]

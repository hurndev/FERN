import asyncio
import time

from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes, ChatTypes
from fern.transport.fake import FakeRelayNetwork


async def main() -> None:
    network = FakeRelayNetwork()
    relay_a, relay_b, relay_c = network.spawn(count=3)

    founder = UserIdentity.generate()
    group = GroupKeypair.generate()

    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group.pubkey,
        author_keypair=founder.keypair,
        parents=(),
        content={
            "name": "Example Group",
            "description": "A FERN example",
            "public": True,
            "founder": founder.pubkey,
            "mods": [founder.pubkey],
            "relays": [relay_a.url, relay_b.url, relay_c.url],
        },
        group_keypair=group.keypair,
    )

    print(f"  Group: {group.pubkey[:16]}...")
    print(f"  Founder: {founder.pubkey[:16]}...")
    print(f"  Genesis: {genesis.id[:16]}...")

    await relay_a.publish(genesis)
    await relay_b.publish(genesis)
    await relay_c.publish(genesis)

    msg = build_event(
        type=ChatTypes.MESSAGE,
        group=group.pubkey,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "Hello, FERN!", "channel": "general"},
        ts=int(time.time()),
    )

    receipt = await relay_a.publish(msg)
    print(f"  Published message: {msg.id[:16]}...")
    print(f"  Receipt from relay: {receipt.relay[:16]}...")

    attestation = await relay_a.request_attestation(group.pubkey)
    print(f"  Attestation count: {attestation.count}")
    print(f"  Attestation set_hash: {attestation.set_hash[:16]}...")


if __name__ == "__main__":
    asyncio.run(main())

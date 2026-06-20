import asyncio

from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair
from fern.transport.fake import FakeRelay


async def main() -> None:
    relay_kp = Keypair.generate()
    relay = FakeRelay(relay_keypair=relay_kp)

    print(f"  Relay: {relay.relay_pubkey[:16]}...")
    print(f"  URL: {relay.url}")
    print("  Relay is ready for events.")

    founder = UserIdentity.generate()
    group = GroupKeypair.generate()

    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group.pubkey,
        author_keypair=founder.keypair,
        parents=(),
        content={
            "name": "Test Group",
            "description": "",
            "public": True,
            "founder": founder.pubkey,
            "mods": [founder.pubkey],
            "relays": [relay.url],
        },
        group_keypair=group.keypair,
    )

    await relay.publish(genesis)
    print(f"  Accepted genesis: {genesis.id[:16]}...")

    events = [e async for e in relay.sync(group.pubkey)]
    print(f"  Events stored: {len(events)}")


if __name__ == "__main__":
    asyncio.run(main())

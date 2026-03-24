Create key pair
User enters group name as group_hash@relay1@relay2@relay3
Request the group genesis event from each relay. Check integrity.
Download all group events from all relays concurrently. Recalculate hash and check signatures.
Compare the list of events received from each relay. If any relay is missing any events, publish those events to the relay until the relays have all events.


Assemble the DAG locally. If there are no gaps, sync is complete.
If there is a gap, request the missing event hash. Continue this process until the gap is filled.


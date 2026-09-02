# Oracle idle guard operational boundary

`oracle_idle_state.sh` performs a read-only classification only. It emits one of
`BUSY`, `IDLE`, `UNKNOWN` and never calls OCI.

`oracle_stop_consumer.py` is an unconnected consumer contract used to prove that
only `IDLE` can reach an injected stop action. Its CLI is decision-only.

Production automation must not be wired to STOP by this change. A future power
consumer must perform a last-second fresh collection and call the same evaluator;
any non-IDLE state must block.

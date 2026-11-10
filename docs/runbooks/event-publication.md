# Runbook: Outbox publication and receipt recovery

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-10-29

## Symptoms

Payments return 201 but outbox rows remain pending/dead, receipt counts stop
increasing, or relay/consumer pods restart after registry/broker/storage errors.

## Impact

Payments and events remain in PostgreSQL. Broker outages do not roll back an
already committed payment or require a new idempotency key. Kafka records may
be redelivered; the receipt consumer applies only one effect per event ID.
No authorization or settlement is performed.

## Diagnosis

Examples use the fixed `rpp` three-cluster topology; pass `MULTI_CLUSTER=3`
to select it. Its profiles are `rpp-1`, `rpp-2`, and `rpp-3`.
Discover the current writer from private role metadata after any promotion;
initially it is `pg-1` on `rpp-1`:

```bash
make dev-status MULTI_CLUSTER=3
kubectl --context rpp-1 -n postgres get pods,pvc
kubectl --context rpp-1 -n apps logs deployment/payment-api-relay -c relay --tail=50
kubectl --context rpp-1 -n apps logs deployment/payment-api-consumer -c consumer --tail=50
kubectl --context rpp-1 -n kafka logs statefulset/payments-kafka -c kafka --tail=50
kubectl --context rpp-1 -n data logs deployment/apicurio -c registry --tail=50
PRIMARY=$(kubectl --context rpp-1 -n postgres get cluster pg-1 -o jsonpath='{.status.currentPrimary}')
kubectl --context rpp-1 -n postgres exec "$PRIMARY" -c postgres -- \
  psql -U postgres -d payments -c \
  'SELECT state, count(*) FROM payment_outbox GROUP BY state; SELECT count(*) AS receipts FROM payment_event_receipts; SELECT topic, partition, record_offset, failure_code FROM payment_event_dead_letters;'
```

Do not print Secret values, Terraform state, raw dead-letter payloads, or database
URLs into logs/tickets. Registry health probes use management port 9000;
its API uses 8080. Worker readiness checks SQL connectivity/migrations, not
proof that every pending event has reached Kafka; inspect outbox state too.

## Mitigation

Restore broker/registry/PostgreSQL availability first. Pending rows retry with
persisted exponential delays and an eight-attempt budget. A dead row remains
durable and blocks later events for the same account; other accounts can proceed.
Do not delete it or create a new event identity to bypass deduplication.

After diagnosis and correction, explicitly requeue an inspected event with SQL:

```sql
UPDATE payment_outbox
SET state = 'pending', attempts = 0, available_at = now(), failure_code = NULL
WHERE event_id = '<reviewed-event-uuid>' AND state = 'dead';
```

Run this using the same profile's PostgreSQL connection. This is a deliberate
operator action, not an automatic unbounded retry. Inspect consumer dead letters
before any manual redrive; malformed records and reused IDs with changed content
must be corrected rather than blindly replayed. No automated quarantine-redrive
command is implemented.

Keep the Kafka PVC, Terraform-generated cluster ID, SCRAM credentials, and
certificate state together. `--ignore-formatted` preserves existing broker
metadata; changing credentials in state alone is not credential rotation.
The local certificate is valid for one year. Plan reviewed certificate rotation
before expiry; never disable TLS verification as a workaround.

## Verification

```bash
make -C modules/payment-api test INTEGRATION=1
make -C modules/event-contracts test
make smoke MULTI_CLUSTER=3 MINIKUBE_PROFILE=rpp-1
```

The Docker-backed recovery test kills a publisher after Kafka acknowledgement and
before SQL acknowledgement, restarts publication, checks stable event identity
and exactly one receipt, and verifies malformed-record quarantine. It also checks
idempotent schema/topic registration, rejected incompatible schemas, and invalid
SCRAM credentials. Local registry schema IDs persist in PostgreSQL, unlike the
disposable integration registry's default in-memory SQL store.

### Three-cluster outage exercise

Use only a disposable local environment: this exercise deliberately stops a
cluster. The fixed topology is selected with `MULTI_CLUSTER=3`; do not create
another profile family. Brokers use `kafka` in contexts `rpp-1`, `rpp-2`, and `rpp-3`.
Each new full-stack member runs apps/registry; SQL writes use the current
writer. Numbered Kafka-only bases retain their cluster-1-only app/database placement.
Keep browser access on an available member running.

Before any outage, confirm all three brokers are ready and the payment topic has
three replicas and three in-sync replicas per partition. Run Kafka CLI commands
inside a broker; the mounted properties contain credentials, so pass the file
without printing it:

```bash
make dev-status MULTI_CLUSTER=3
kubectl --context rpp-1 -n kafka exec payments-kafka-0 -c kafka -- \
  env 'KAFKA_HEAP_OPTS=-Xms32m -Xmx64m' \
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$(minikube -p rpp-1 ip):30092" \
  --command-config /etc/kafka/tls/client.properties \
  --describe --topic payments.initiated.v1
```

The node IP is intentional: the certificate covers node IPs, not loopback.
Terraform mounts a separate private client config for the Kafka CLI. Never print
it, copy credentials into terminal history, or disable hostname verification.

Stop cluster 2, not cluster 1, to leave applications and registry available:

```bash
minikube stop -p rpp-2
# Create a payment with a fresh Idempotency-Key through cluster 1, then inspect SQL.
minikube start -p rpp-2
make dev-status MULTI_CLUSTER=3
```

Verify the new payment reaches `published` and one receipt while cluster 2 is stopped;
after restart verify ISR returns to three. A restart can also temporarily
rebalance consumers. Two lost brokers remove quorum/minimum ISR: do not claim
successful publication, and inspect/requeue exhausted outbox rows only after
recovery. HTTP success alone does not prove a broker acknowledgement.

Preserve node IPs, voter IDs, PVCs, and credentials. If startup reports an
inotify failure, use the [host prerequisite](../developer-setup.md#stage-3-optional-three-cluster-kafka-lab);
increasing Java heap will not fix a failed Kubernetes control plane.
On 2026-10-30, isolated `rpp-multi-check` verification stopped the then-named C member while it was
the active controller. A new payment still reached published state and one
receipt with ISR two. Restarting C restored ISR three for every partition.
Dirty-source rollout, restored-source rollout, API pod recovery, full three-cluster
teardown/recreation, and a new post-recreation publication/receipt passed.
Only the temporary lab was deleted afterward; the original `rpp-try` context
was restored. Two-cluster outages and sustained stress were not tested.

### Cross-cluster PostgreSQL recovery

These commands apply to the fixed
[full-stack lab](../developer-setup.md#stage-3-full-stack-three-cluster-lab),
not the older Kafka-only lab.
Examples use the `rpp` topology selected with `MULTI_CLUSTER=3`. Preserve all
`.local/rpp-multi/` and per-profile state. Run topology changes serially.

Inspect the writer and synchronous peers without printing credentials:

```bash
PRIMARY=$(kubectl --context rpp-1 -n postgres get cluster pg-1 -o jsonpath='{.status.currentPrimary}')
kubectl --context rpp-1 -n postgres exec "$PRIMARY" -c postgres -- \
  psql -U postgres -d payments -c \
  'SELECT pg_is_in_recovery(); SHOW synchronous_standby_names; SELECT application_name,state,sync_state FROM pg_stat_replication;'
```

Initially `pg-1` reports recovery `false`, `ANY 1 ("pg-2","pg-3")`, and two streaming
quorum standbys. Both `pg-2` and `pg-3` must report recovery `true` and share the writer's database
system identifier. The SQL-backed registry uses the same primary in every
profile. Count published outbox rows and receipts on this database rather than
the old single-cluster StatefulSet.
With additional `postgres_instances`, inspect the designated primary in each
cluster using `.status.currentPrimary`; local pod ordinals are not stable.
Local cascading standbys do not satisfy the required remote acknowledgement.

#### Planned switchover

```bash
make db-switchover MULTI_CLUSTER=3 DATABASE_TARGET=pg-2
```

All three members must be healthy initially. The coordinator demotes the former
primary, waits for CNPG's demotion token, promotes the requested target using
that token, verifies it is no longer in recovery, redirects the third member,
and reconciles application connections in all profiles. The private transition
journal makes interrupted operations resumable: retry the **same** target.
Ordinary updates refuse an incomplete transition. Never remove the journal to
force another promotion. A successful role is persisted, so `dev-update`
does not undo a promotion.

#### Fenced failover

```bash
make db-failover MULTI_CLUSTER=3 DATABASE_TARGET=pg-3
make dev-access MULTI_CLUSTER=3 MINIKUBE_PROFILE=rpp-3 LOCAL_PORT=18082
```

The command first verifies owned Docker identities, disables the old primary
node's restart policy, stops it, disconnects its owned network, and verifies both
fences. Unreachability alone is not fencing. It then inspects **both** surviving
standbys: they must be read-only and share database system/timeline identities.
The requested target must hold the greatest durably received WAL position
among survivors, and must replay through that position before promotion.
If another survivor is ahead, choose that member; if either survivor cannot
be inspected, the operation refuses to claim safe promotion. It does not force
an arbitrary or asynchronous candidate.

After promotion it redirects the surviving standby and applications. The old
profile stays stopped/disconnected. `dev-up` refuses to restart it;
`dev-update` reconciles only survivors while the fence is complete.
If failover is interrupted, preserve its fence journal and retry the same target.
If promotion completed but application rollout failed, use `dev-update`.
Do not run `minikube start`, individual `dev-up`, or Docker network/restart
commands against the fenced profile.

This procedure assumes the two surviving standby disks are intact; it does not
provide disaster recovery from loss of the host or both standby volumes.
The host remains the only physical failure domain.

#### Former-primary rejoin

```bash
make db-rejoin
```

**Destructive to the fenced owned profile only:** this deletes/recreates that
Minikube node and its old local volumes, then clones PostgreSQL from the current
writer. It never briefly restarts the divergent old database. Kafka restores
the missing broker from its surviving quorum; applications return with current
writer credentials. The command verifies the rebuilt database is read-only and
has the current writer's system identifier before removing the fence guard.
Retry the command after a partial rebuild; do not delete recovery metadata.
Normal whole-lab teardown requires rejoin first, so Terraform can reach all
three Kubernetes APIs. Successful `dev-clean` resets database role metadata
for an empty fresh topology; it deletes **all** lab payments and schema IDs.

After either recovery path verify exactly one writer, streaming peers,
acknowledged payment IDs/idempotent replays, published outbox rows, one receipt
per event, registry schema IDs, and Kafka ISR recovery. HTTP success alone does
not verify completed event publication.

## Prevention and follow-up

Never edit applied SQL or compatibility baselines. Append schema versions and
check compatibility against every retained baseline. `make dev-update` preserves
Kafka/PostgreSQL data; `make dev-clean` deletes both through namespace/PVC removal.
Use a dedicated profile for outage/recreation tests. Kafka replication factor one
does not provide broker failover or protect against volume loss.
Production access controls, backup/rotation, and observability follow their
planned later stages.

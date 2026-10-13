---
id: rb-memory-leak
title: Memory leak or memory exhaustion
fault_type: mem
allowed_actions: [restart_service, rollback_service, escalate_to_human]
---

## Symptoms
- Resident memory of one container grows steadily and does not come back down.
- The process gets close to its memory limit; it may be OOM-killed and restart.
- Garbage-collection pauses make responses from that service slow and uneven.
- Processor and network usage do not explain the slowdown.

## How to confirm
- Look at the memory working set of the container: incident mean far above baseline.
- Check for restarts or out-of-memory messages in that service's logs.
- Make sure the growth is in one service, not cluster-wide.

## Fix
- Restart the service (`restart_service`) to release memory and buy time.
- If the leak started after a release, roll back (`rollback_service`).

## When to escalate
- Memory climbs again within minutes of a restart, or no recent release explains it.

---
id: rb-socket-exhaustion
title: Socket or connection exhaustion
fault_type: socket
allowed_actions: [restart_service, scale_service, escalate_to_human]
---

## Symptoms
- One service holds an unusual number of open sockets or connections.
- New connections to it are refused or hang while existing ones still work.
- Callers report "connection refused", "too many open files" or connection-pool timeouts.
- Network socket counters jump while request volume is about the same.

## How to confirm
- Compare socket and file-descriptor metrics for the container with the baseline window.
- Check the call graph: failures should sit on edges into this service.
- Look for connection errors in the callers' logs that name this service.

## Fix
- Restart the service (`restart_service`) to drop leaked connections.
- Add replicas (`scale_service`) if connection limits are being hit by real load.

## When to escalate
- Sockets leak again right after a restart, which points to a code bug.

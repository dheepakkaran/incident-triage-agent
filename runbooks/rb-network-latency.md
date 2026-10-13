---
id: rb-network-latency
title: Network delay into a service
fault_type: delay
allowed_actions: [restart_service, escalate_to_human]
---

## Symptoms
- Every hop that calls one particular service takes much longer than it used to.
- The service itself does little extra work: its processor and memory look normal.
- Errors stay low at first; time spent waiting on the wire grows instead.
- Callers that do not depend on that service keep their normal speed.

## How to confirm
- In the traces, compare span durations on the edges into the suspect service with the baseline.
- Check that the slowdown is on the caller-to-callee edge, not inside the caller.
- Confirm the suspect's own resource metrics did not change much.

## Fix
- Restart the service (`restart_service`) so it is rescheduled onto a healthy node and network path.
- Check recent network policy or sidecar changes for that pod.

## When to escalate
- Several unrelated services slow down together, which points at the node or the network itself.

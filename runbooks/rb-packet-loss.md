---
id: rb-packet-loss
title: Packet loss on a service's network path
fault_type: loss
allowed_actions: [restart_service, escalate_to_human]
---

## Symptoms
- Some calls to one service fail or need retries, so callers see intermittent errors.
- Traffic in and out of that container drops, or retransmissions rise.
- Response times become spiky: most calls are fine, some take seconds.
- Logs mention connection resets, unavailable upstreams or deadline exceeded.

## How to confirm
- In the traces, look for a higher error rate on edges into the suspect service.
- Compare network receive/transmit metrics for that container with the baseline.
- Check that the failures cluster around one callee, not around one caller.

## Fix
- Restart the service (`restart_service`) to move it to a healthy network path.
- Check the node's network interface and any recent mesh or firewall changes.

## When to escalate
- Loss is seen on many services at once, or persists after the pod moves.

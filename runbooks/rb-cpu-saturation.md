---
id: rb-cpu-saturation
title: CPU saturation in a service container
fault_type: cpu
allowed_actions: [scale_service, escalate_to_human]
---

## Symptoms
- One container burns far more processor time than usual and sits near its CPU limit.
- Requests to that service queue up, so its callers see slow responses and timeouts.
- Throttling counters climb; memory and network usage stay roughly flat.
- Upstream services look slow too, but their own processor usage is normal.

## How to confirm
- Compare the container's CPU usage now with the 10 minutes before the incident.
- Check that the slow edges in the call graph all point *into* this service.
- Rule out a traffic spike: request counts into the service should be similar to baseline.

## Fix
- Add replicas (`scale_service`) to spread the load while the hot code path is found.
- If a recent change caused a busy loop, hand over to the owning team.

## When to escalate
- CPU stays pinned after scaling, or several services saturate at the same time.

---
id: rb-bad-deploy
title: Faulty release or code-level error
fault_type: code
allowed_actions: [rollback_service, escalate_to_human]
---

## Symptoms
- A service starts throwing exceptions or returning server errors right after a release.
- Stack traces or the same error message repeat many times in its logs.
- Resource usage looks normal; the failures are in the application logic.

## How to confirm
- Group the service's error logs into templates and compare counts with the baseline.
- Check that errors began at a deploy time, not gradually.
- Make sure the failing requests are handled by this service, not passed through it.

## Fix
- Roll back to the previous version (`rollback_service`).

## When to escalate
- No recent release, or rolling back does not stop the errors.

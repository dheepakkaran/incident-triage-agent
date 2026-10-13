---
id: rb-disk-pressure
title: Disk I/O pressure on a service
fault_type: disk
allowed_actions: [restart_service, escalate_to_human]
---

## Symptoms
- Bytes read from or written to the filesystem by one container jump far above normal.
- Calls that touch local storage or temporary files become slow.
- I/O wait rises while processor and memory usage stay modest.

## How to confirm
- Compare the container's filesystem read/write metrics with the baseline window.
- Check whether the slow requests are the ones served by this service.
- Look for warnings about slow writes, full volumes or flush delays in its logs.

## Fix
- Restart the service (`restart_service`) to stop a runaway writer and clear temp files.
- Free space or move heavy batch jobs off the node.

## When to escalate
- The volume is shared with other workloads, or I/O stays high after a restart.

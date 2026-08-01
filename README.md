# ZhongHongHVAC
Python driver for ZhongHong HVAC Controller

## Connection robustness

The gateway connection is self-healing:

- Unknown protocol values (for example fan speed `0x00` reported by an indoor
  unit right after power-off) are tolerated instead of crashing the listener.
  A malformed frame is logged and skipped while the rest of the buffer is
  still processed.
- The listener thread survives unexpected errors and keeps running.
- When no status data has been received for `probe_interval` seconds the hub
  sends a status query as a health probe. After `max_probe_failures`
  consecutive failed probes the TCP connection is reopened.
- `ZhongHongGateway.connected` reflects the health of the connection so
  integrations can mark entities unavailable instead of showing stale values.

Tunables (instance attributes, set before `start_listen()`):

| Attribute | Default | Meaning |
|-----------|---------|---------|
| `_recv_timeout` | 30.0 | socket read timeout in seconds |
| `_probe_interval` | 60.0 | seconds without data before probing |
| `_probe_response_timeout` | 10.0 | seconds to wait for a probe response |
| `_max_probe_failures` | 3 | consecutive probe failures before reopening the socket |
| `_stale_timeout` | 300.0 | seconds without data before `connected` turns `False` |

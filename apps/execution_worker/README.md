Execution Worker (Placeholder)

This directory is reserved for a Windows-side worker (or Linux+Wine) that maintains
terminal connectivity and serializes broker calls. In this scaffold, the functionality
is represented in-process via the `ExecutionGateway` + `PyMT5Adapter`.

Future: split into a separate service reachable by the `bridge-gateway` over HTTP.

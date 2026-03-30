# P1-010 Solution 3 - security and doc cleanup

Objective:

Close the remaining Solution 3 security and documentation gaps without changing its RFC-0003 architecture.

Acceptance criteria:

- [x] Reject unsafe webhook callback targets so authenticated users cannot drive internal-network SSRF.
- [x] Stop writing plaintext API keys into admin outbox payloads.
- [x] Refresh Solution 3 docs and RFC status to match the shipped tree and current implementation.
- [x] Remove the dead empty `docker/reaper` directory.
- [x] Add or update targeted tests for the new security behavior.

TDD order:

1. Add red tests for unsafe callback URLs.
2. Add red test for admin outbox payload masking/sanitization.
3. Implement webhook target validation and payload sanitization.
4. Refresh README/RFC/tree docs and cleanup dead directory.
5. Re-run targeted quality/test commands and final proof.

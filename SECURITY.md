# Reporting a vulnerability

Please use the repository's private security-advisory channel and include the affected build, reproduction steps, impact, and whether a credential or tailnet peer is required. Do not publish a credential, pairing fragment, pending capability, private desktop state, or live endpoint in a public issue.

If private advisories are unavailable, open a minimal public issue asking the maintainer for a private contact without including exploit details.

The implemented threat model and release gate are in [docs/SECURITY.md](docs/SECURITY.md). Shell/command injection, lock bypass, wildcard binding, credential disclosure, unauthorized scope use, upload smuggling/ambiguity, unbounded memory or disk reservation, active-content acceptance, path escape, overwrite, unsafe file mode/ownership, incomplete staging retention, privileged packaging/runtime behavior, tailnet enrollment or Tailscale SSH mutation, unowned Tailscale mutation, and orphaned reachable endpoints are release-blocking severity.

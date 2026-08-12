# Security Policy

## Supported versions

Security fixes are provided for the latest beta release until a stable release exists.

## Reporting

Do not open a public issue for a vulnerability that could expose local files, execute commands outside the Skill workflow, bypass loopback binding, or write outside an approved installation directory. Report it privately through GitHub Security Advisories after the repository is published.

## Trust boundary

The project is a local single-user tool. It assumes the operating-system account and host agent are trusted. The HTTP service binds only to `127.0.0.1`, has no authentication, and must not be exposed through a public proxy, container port mapping, or shared host.

The browser page is read-only, but the localhost API accepts writes from local processes. Message content is persisted in local state. Do not use the service for secrets or regulated data without an appropriate local security review.

The installer intentionally defaults to dry-run, rejects symbolic links, and refuses replacement without an explicit flag. Review all downloaded Skill instructions and scripts before applying installation.

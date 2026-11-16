# Security Policy

## Supported versions

Security fixes go into the latest release.

| Version | Supported |
| ------- | --------- |
| 1.x     | Yes       |
| < 1.0   | No        |

## Reporting a vulnerability

Please report vulnerabilities privately: open the repository's **Security** tab and choose
**Report a vulnerability**
([direct link](https://github.com/FareedKhan-dev/production-grade-agentic-system/security/advisories/new)).
Please don't open a public issue for something an attacker could use.

A useful report says what an attacker can do, which files and lines are involved, and how to
reproduce it. Confirmed issues are fixed in a release, and the advisory credits the reporter
unless they prefer otherwise.

## Deploying safely

- Set `JWT_SECRET_KEY` to a long random value:
  `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Anyone who knows the key can
  sign a token for any user, so the API refuses to start with a missing, short or published one.
- Changing `JWT_SECRET_KEY` signs everyone out, because tokens signed with the old key stop
  working. Change it if it may have leaked. If you ran a version before 1.0.0 with Docker Compose,
  change it once: that compose file could put a published default key in place of yours.
- Keep `.env.*` files out of version control and out of images. `.gitignore` and `.dockerignore`
  already exclude them.
- `docker-compose.yml` publishes PostgreSQL, Prometheus, Grafana and cAdvisor on 127.0.0.1 only.
  Put the API behind a reverse proxy with TLS before exposing it to the internet.

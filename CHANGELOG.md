# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [1.0.0] - 2026-11-16

The first release. The code as published could not start (see Fixed), so 1.0.0 is the
first version that runs, and it is checked end to end: by CI on every push, and once
against a real model (Qwen2.5 7B on Ollama) with tool calls, streaming and long-term
memory.

### Security

- **The API refuses to start with an unsafe JWT secret** ([#4], reported by @28Hus).
  `docker-compose.yml` filled in `JWT_SECRET_KEY=supersecretkeythatshouldbechangedforproduction`
  whenever compose could not see the variable itself, and because `environment` wins
  over `env_file`, that published value replaced the key in the env file; nothing
  rejected it, so anyone could sign a token for any user. The fallback is gone. The
  settings check the key at startup and the API refuses a missing key, one shorter
  than the algorithm needs (32 bytes for HS256), or any value published as an example
  in this repository, its README or the article. Signing and verifying refuse an
  unsafe key too, and `.env.example` no longer ships a placeholder key, so an unedited
  copy fails closed. **If you ran an earlier version with Docker Compose, change
  `JWT_SECRET_KEY`.**
- Tokens carry a type, `user` or `session`, and each endpoint accepts one. Invalid,
  expired or wrong-type tokens get 401 (some got 404 or 422 before).
- JWTs are handled by PyJWT instead of python-jose.
- The Docker image no longer contains `.env` files: there was no `.dockerignore`, so
  `COPY . .` copied the env files, API keys included, into every image. A
  `.gitignore` now keeps them out of git as well, and `scripts/build-docker.sh` no
  longer passes the secrets as build arguments.
- Chat requests accept only `user` messages. A client could send `system` messages,
  replacing the system prompt, or invent `assistant` turns.
- Error responses no longer contain `str(e)`. Errors from the OpenAI client or the
  database driver can include masked keys or hostnames; the details now go to the logs.
- PostgreSQL, Prometheus, Grafana and cAdvisor listen on 127.0.0.1 in
  `docker-compose.yml`, and the Grafana admin password comes from
  `GRAFANA_ADMIN_PASSWORD` instead of being `admin` in the file.
- CORS allows credentials only for explicitly listed origins (with `*`, Starlette echoes
  the origin of any website's cookie-carrying request).
- Login checks unknown emails against a dummy hash, so response times do not reveal
  which emails have accounts.
- The logging middleware binds IDs only from tokens with a valid signature.
- `SECURITY.md` replaces GitHub's placeholder template (it listed versions 4.0 to 5.1)
  with how to report privately and how to deploy safely.
- `.env.example` sets `MEM0_TELEMETRY=False`: mem0 otherwise sends usage telemetry to
  PostHog.

### Fixed

- **The application could not start.** `src/main.py` imported `src.interface.api`
  (the router is `src/interface/router.py`) and the tools package imported
  `.duckduckgo_search` (the file is `web_search.py`).
- **Docker:** the image ran `app.main:app`, a module that does not exist; compose
  mounted the nonexistent `./app` over `/app/src`, hiding the code behind an empty
  folder; the health check called `curl`, which the slim image does not have; and the
  dependency step ran `uv pip install -e .` before any source was copied. The image now
  installs from `uv.lock` with `uv sync --frozen`. The Makefile's run targets used
  `app.main:app` too, and `make eval ENV=...` ignored `ENV`.
- **Login failed for passwords containing `&`, `<`, `>`, `"` or `'`.** The password was
  HTML-escaped at login but not at registration, so it never matched. Logins also
  treated the email as case-sensitive, while registration stores it lowercased.
- **An answer longer than 3,000 characters failed with a 500**, and so did every later
  request for that session's history: the request limit applied to the assistant's
  replies as well. Requests and responses now have separate schemas.
- **The agent forgot the conversation after one long answer.** History was trimmed to
  `MAX_TOKENS`, the cap on a single answer. It now has its own budget,
  `MAX_CONTEXT_TOKENS` (default 16,000), and tokens are estimated instead of counted
  with a tokenizer, which works the same for every model.
- **The stream sent the raw search results** to the client as part of the answer, and
  could not handle models that return content blocks. It now carries only the chat
  node's text.
- **Every streamed answer ended in an error:** the stream built Langfuse's
  `CallbackHandler` with Langfuse 2 arguments, which Langfuse 3 rejects. The user and
  session now reach Langfuse the way version 3 reads them.
- After a fallback to another model, the agent lost its tools, so it could no longer
  search. Every model switched to gets the tools bound again.
- Retries applied to errors that cannot succeed on retry (bad requests, missing models).
  Only rate limits, timeouts, dropped connections and 5xx errors are retried now.
- `get_response` swallowed errors and returned `None`, which surfaced as a confusing
  validation error.
- Background memory updates were started with `asyncio.create_task` without keeping a
  reference, so they could be garbage collected before finishing.
- Memory search results were printed to stdout.
- **Database queries and bcrypt ran on the event loop**, so every login and query
  stalled all other requests. Both now run in worker threads, and bcrypt has a thread
  pool of its own, one thread short of the core count: sharing asyncio's default pool,
  a burst of logins kept the database queries waiting and starved the event loop, which
  in the load test made waiting chats time out.
- The database URL did not escape the credentials, so passwords containing `@`, `/` or
  `:` broke the connection. The auth endpoints also opened a second engine (a second
  connection pool).
- Deleting a session left its conversation in LangGraph's checkpoint tables; it is now
  deleted too, and the session's tokens stop working.
- Renaming a session stored the name HTML-escaped, so listing escaped it twice, and a
  name over 100 characters broke `GET /sessions` for that user. Names are now stored as
  given and capped at 100 characters.
- On a fresh server, each of the first concurrent requests built its own graph and
  connection pool. A lock makes them share one, and the mem0 client is built once the
  same way.
- mem0 runs its blocking model and embedding calls in asyncio's default thread pool,
  which holds only CPU count + 4 threads. That pool, shared with the database queries,
  is `THREAD_POOL_SIZE` (64) threads now.
- uvicorn closes idle keep-alive connections after 5 seconds, the same moment httpx
  stops reusing them, so in the load test some requests went out on a socket the server
  was closing. The image keeps connections alive for 75 seconds.
- The tool node crashed on a tool name the model made up; the model is now told the tool
  does not exist.
- `LOG_LEVEL` was ignored: the level came from `DEBUG` alone, so production logged at
  INFO.
- Request metrics used the raw path as a label (one time series per session ID); they
  use the route template now. The LLM latency buckets stopped at 5 and 10 seconds, so
  most calls landed in `+Inf`; they reach 30 and 60 seconds. The `db_connections` gauge
  was never set and now reports the pool, and a leftover `orders_processed` counter is
  gone.
- **The Grafana dashboard did not load.** Its file used the HTTP API's format (a
  `{"dashboard": ..., "overwrite": true}` wrapper), which file provisioning does not read,
  and no Prometheus datasource was provisioned for it to query. The dashboard is in the
  provisioning format now (with timeseries panels, plus requests per route and database
  connections), and `grafana/datasources/prometheus.yml` provisions the datasource.
- `schema.sql` was SQLite syntax that PostgreSQL rejects. It is now generated DDL, and a
  test keeps it in step with the models.
- Evals: a tool message without OpenAI-specific fields or an empty trace crashed the
  run, the judge used the removed `beta` parse API, and the Langfuse host setting was
  ignored.
- `scripts/logs-docker.sh` and `scripts/stop-docker.sh` looked for a container name that
  compose never creates.
- The API container's open-file limit is raised to 65,536. The default soft limit inside
  a container (1,024 on the test machine) made requests fail with "Too many open files"
  past about a thousand concurrent clients in the load test.

### Changed

- **Breaking:** chat requests accept `role: "user"` only; tokens from earlier versions are
  rejected (they have no type, and the secret should be rotated anyway); `API_V1_STR`
  defaults to `/api/v1`, as `.env.example` and the README already said;
  `JWT_ACCESS_TOKEN_EXPIRE_DAYS` defaults to 7 instead of 30; stream events have an
  `error` field.
- Dependencies are current, each with a ceiling at its next major version, and
  `uv.lock` is regenerated: LangGraph 1.2, LangChain 1.8, Langfuse 3.15, mem0 2.2,
  openai 3.51, FastAPI 0.163, Starlette 1.10, SQLAlchemy 2.1, bcrypt 5 and psycopg 3.3.
  Removed: supabase, passlib, python-jose, psycopg2-binary, pydantic-settings, asgiref,
  duckduckgo-search, langchain-community and starlette-prometheus.
- mem0 2 changed its API: `search` takes the user in `filters`, and `from_config` returns
  the client instead of a coroutine. mem0 2 also creates its entity store and the
  pgvector extension on first use, from several threads at once when requests arrive
  together, and those statements collide; the API creates both up front.
- The web search tool calls `ddgs` directly (langchain-community is being sunset), is
  named `web_search`, and reports a failed search to the model instead of failing.
- The monitoring images are pinned to the versions this release was tested with
  (Prometheus 3.16.0, Grafana 13.3.1, cAdvisor 0.55.1) instead of `latest`.
- Any OpenAI-compatible server works: `OPENAI_BASE_URL`, any model name in
  `DEFAULT_LLM_MODEL`, `LLM_FALLBACK_MODELS` for the fallback order, and
  `LONG_TERM_MEMORY_EMBEDDER_DIMS` for embedders other than OpenAI's.
- The README follows the `src/` layout, its listings match the fixed code, and the stress
  test section is re-run with a script that works (see Added). It no longer says that
  every request is rate-limited: SlowAPI limits the decorated endpoints (registration,
  login, chat, messages), and applies `RATE_LIMIT_DEFAULT` to the others only through
  its middleware, which the app does not add. The README explains how to add it.

### Added

- Tests: 57 of them, against a real PostgreSQL, with the LLM, web search and long-term
  memory replaced by fakes. For 16 of the fixes above, putting the old behaviour back
  makes the test written for it fail.
- CI (`.github/workflows/ci.yml`): ruff, the tests, and a Docker job that checks the
  stack refuses to start without a safe secret, then starts it and signs up, logs in and
  opens a session.
- `scripts/stress_test.py`, a load test that registers its users, and
  `scripts/fake_llm.py`, an OpenAI-compatible stand-in that answers after a fixed delay.
- Log events for tool calls (`tool_called`) and failed web searches
  (`web_search_failed`), a Langfuse flush at shutdown, and an index on `session.user_id`.
- `.gitattributes`, so shell scripts stay LF on Windows checkouts (the container cannot
  run a CRLF entrypoint).

[#4]: https://github.com/FareedKhan-dev/production-grade-agentic-system/issues/4
[1.0.0]: https://github.com/FareedKhan-dev/production-grade-agentic-system/releases/tag/v1.0.0

"""Load test: many users log in, open a chat session and ask a question, all at once.

Every simulated user is registered first (that part is not measured). Then all of them, at
the same moment: log in, create a session, send one chat message. The script prints, for
each step, how many requests succeeded, what the others got, and the latency percentiles.

    uv run --group test python scripts/stress_test.py --base-url http://localhost:8000/api/v1 --users 1500

Every simulated user comes from this machine's IP address, and the API's rate limits count
per IP (10 registrations an hour, 20 logins a minute by default). Raise RATE_LIMIT_REGISTER,
RATE_LIMIT_LOGIN and RATE_LIMIT_CHAT in the API's env file for the test, or most requests
will be answered with 429. To measure the system rather than an LLM provider, point the API
at scripts/fake_llm.py.
"""

import argparse
import asyncio
import time
import uuid
from collections import (
    Counter,
    defaultdict,
)

import httpx

PASSWORD = "StrongPassword123!"
STEPS = ("login", "session", "chat")


async def timed(results, step, request):
    """Await a request, record its status and latency under `step`, and return the response if it succeeded."""
    start = time.perf_counter()
    try:
        response = await request
        status = response.status_code
    except httpx.HTTPError as e:
        response, status = None, type(e).__name__
    results[step].append((status, time.perf_counter() - start))
    return response if status == 200 else None


async def simulate_user(client, base_url, email, message, results):
    """Log in, create a chat session and send one message, as one user."""
    login = await timed(
        results,
        "login",
        client.post(f"{base_url}/auth/login", data={"username": email, "password": PASSWORD, "grant_type": "password"}),
    )
    if login is None:
        return
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    session = await timed(results, "session", client.post(f"{base_url}/auth/session", headers=user_headers))
    if session is None:
        return
    session_headers = {"Authorization": f"Bearer {session.json()['token']['access_token']}"}

    await timed(
        results,
        "chat",
        client.post(
            f"{base_url}/chatbot/chat",
            json={"messages": [{"role": "user", "content": message}]},
            headers=session_headers,
        ),
    )


def percentile(values, fraction):
    """Return the value below which `fraction` of the sorted values fall."""
    values = sorted(values)
    return values[min(len(values) - 1, int(fraction * len(values)))]


def report(results, users):
    """Print one line per step: successes, other outcomes and latency percentiles."""
    print(f"{'step':<9}{'ok':>11}  {'p50':>7}{'p95':>8}{'p99':>8}{'max':>8}  other answers")
    for step in STEPS:
        entries = results[step]
        latencies = [seconds for status, seconds in entries if status == 200]
        others = Counter(status for status, _ in entries if status != 200)
        other_text = ", ".join(f"{status} x{count}" for status, count in others.most_common()) or "-"
        if latencies:
            timing = "".join(
                f"{value:>7.2f}s" for value in (percentile(latencies, 0.5), percentile(latencies, 0.95), percentile(latencies, 0.99), max(latencies))
            )
        else:
            timing = f"{'-':>8}" * 4
        print(f"{step:<9}{len(latencies):>6}/{users:<5}{timing}  {other_text}")


async def main():
    """Parse the arguments, register the users, then run every user at once."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1")
    parser.add_argument("--users", type=int, default=1500)
    parser.add_argument("--message", default="Explain quantum computing briefly.")
    parser.add_argument("--timeout", type=float, default=600, help="seconds before a request is abandoned")
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=args.users, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(limits=limits, timeout=args.timeout) as client:
        # Setup, not measured: create the accounts, 50 at a time
        emails = [f"load-{uuid.uuid4().hex[:12]}@example.com" for _ in range(args.users)]
        registered = []
        gate = asyncio.Semaphore(50)

        async def register(email):
            async with gate:
                response = await client.post(f"{args.base_url}/auth/register", json={"email": email, "password": PASSWORD})
                if response.status_code == 200:
                    registered.append(email)

        start = time.perf_counter()
        await asyncio.gather(*(register(email) for email in emails))
        print(f"Registered {len(registered)}/{args.users} users in {time.perf_counter() - start:.1f}s")
        if not registered:
            raise SystemExit("No user could register; check the base URL and RATE_LIMIT_REGISTER.")

        # The test: every user at once
        print(f"Starting {len(registered)} users at once...")
        results = defaultdict(list)
        start = time.perf_counter()
        await asyncio.gather(*(simulate_user(client, args.base_url, email, args.message, results) for email in registered))
        elapsed = time.perf_counter() - start

    print()
    report(results, len(registered))
    answered = sum(1 for status, _ in results["chat"] if status == 200)
    print(f"\n{answered} of {len(registered)} users got an answer; the whole run took {elapsed:.1f}s.")


if __name__ == "__main__":
    asyncio.run(main())

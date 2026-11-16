"""How the agent builds its graph and its memory client: once, however many requests ask at once."""

import asyncio
import time

from langgraph.checkpoint.memory import InMemorySaver

import src.agent.workflow as workflow


def test_concurrent_first_requests_build_one_graph(monkeypatch):
    agent = workflow.LangGraphAgent()
    pools_opened = []

    async def slow_pool():
        pools_opened.append(1)
        await asyncio.sleep(0.05)  # opening a pool takes a while; more requests arrive meanwhile
        return object()

    class FakeSaver(InMemorySaver):
        def __init__(self, pool):
            super().__init__()

        async def setup(self):
            pass

    monkeypatch.setattr(workflow, "AsyncPostgresSaver", FakeSaver)
    monkeypatch.setattr(agent, "_get_connection_pool", slow_pool)

    async def ten_requests():
        return await asyncio.gather(*(agent.create_graph() for _ in range(10)))

    graphs = asyncio.run(ten_requests())
    assert len(pools_opened) == 1
    assert all(graph is graphs[0] for graph in graphs)


def test_concurrent_requests_build_one_memory_client(monkeypatch):
    # mem0 2 builds the client synchronously; awaiting from_config (the mem0 1 API) fails
    agent = workflow.LangGraphAgent()
    builds = []

    def slow_from_config(config):
        builds.append(config)
        time.sleep(0.05)
        return object()

    monkeypatch.setattr(workflow.AsyncMemory, "from_config", staticmethod(slow_from_config))

    async def ten_requests():
        return await asyncio.gather(*(agent._long_term_memory() for _ in range(10)))

    clients = asyncio.run(ten_requests())
    assert len(builds) == 1
    assert all(client is clients[0] for client in clients)
    assert builds[0]["vector_store"]["config"]["embedding_model_dims"] == builds[0]["embedder"]["config"]["embedding_dims"]

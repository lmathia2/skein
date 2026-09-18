from harness.ptc.repl import PersistentPythonWorker


class Broker:
    def read(self, path, offset=1, limit=400):
        return {"status": "ok", "data": {"text": "answer = 42\n"}}

    def write(self, *args, **kwargs): return {"status": "ok"}
    def edit(self, *args, **kwargs): return {"status": "ok"}
    def bash(self, *args, **kwargs): return {"status": "ok", "data": {"stdout": ""}}
    def call(self, *args, **kwargs): raise ValueError("unsupported")
    def parallel(self, operations): return []
    def artifacts_load(self, *args, **kwargs): raise ValueError("unsupported")
    def artifacts_list(self): return []
    def artifacts_publish(self, *args, **kwargs): raise ValueError("unsupported")


def test_skein_worker_persists_state_across_pi_cells():
    with PersistentPythonWorker(max_output_bytes=16_000) as worker:
        first = worker.execute("saved = agent.fs.read('x')['data']['text']", Broker())
        second = worker.execute("print(saved)", Broker())
    assert first.status == "ok"
    assert second.status == "ok"
    assert second.stdout == "answer = 42\n\n"

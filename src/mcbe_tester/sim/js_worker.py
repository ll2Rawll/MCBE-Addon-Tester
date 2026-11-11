"""QuickJS process. Only a bounded JSON RPC bridge is exposed, never host modules.

The parent enforces a wall deadline and kills this process on runaway execution.
QuickJS additionally bounds its own heap/stack. No network or filesystem functions
are registered in the JavaScript context.
"""

import json
import sys

import quickjs

MAX_LINE = 32 * 1024 * 1024


def read():
    line = sys.stdin.readline(MAX_LINE + 1)
    if not line or len(line) > MAX_LINE:
        raise RuntimeError("Invalid/oversized bridge message")
    return json.loads(line)


def send(data):
    payload = json.dumps(data, allow_nan=False)
    if len(payload) > MAX_LINE:
        raise RuntimeError("Bridge response budget exceeded")
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def main():
    context = quickjs.Context()
    context.set_memory_limit(64 * 1024 * 1024)
    context.set_max_stack_size(512 * 1024)

    def rpc(method, args):
        send({"rpc": method, "args": json.loads(args)})
        return json.dumps(read(), allow_nan=False)

    context.add_callable("__host_rpc", rpc)
    dispatch = None
    while True:
        try:
            message = read()
        except (EOFError, RuntimeError):
            break
        try:
            if message["op"] == "close":
                break
            if message["op"] == "boot":
                context.eval(message["bootstrap"])
                dispatch = context.get("__sim_dispatch")
                context.eval("delete globalThis.__sim_dispatch; delete globalThis.__host_rpc;")
                context.eval(message["code"])
                value = None
            else:
                value = dispatch(message["op"], json.dumps(message.get("data"), allow_nan=False))
            jobs = 0
            while context.execute_pending_job():
                jobs += 1
                if jobs >= 2048:
                    raise RuntimeError("Promise job budget exhausted")
            errors = json.loads(dispatch("promiseErrors", "null"))
            if errors:
                raise RuntimeError("Unhandled script rejection: " + "; ".join(errors))
            send({"ok": True, "value": value, "jobs": jobs})
        except Exception as exc:
            send({"ok": False, "error": str(exc)[:4000]})


if __name__ == "__main__":
    main()

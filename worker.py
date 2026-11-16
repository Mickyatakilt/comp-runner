"""Comp job worker. Polls the CRM queue (via the comp-worker edge function), runs fast_comps (Codex on ChatGPT plan),
posts the result back. Runs anywhere with Codex logged in: Mac now, GitHub Actions later.
  python worker.py            # loop forever (poll every 10 s)
  python worker.py --once     # process queued jobs then exit (for cron / Actions)
Secret: env COMP_WORKER_SECRET or file .worker_secret next to this script. Never print it."""
import json, os, subprocess, sys, time
import fast_comps

HERE = os.path.dirname(os.path.abspath(__file__))
FN = "https://jwctfsapxrdzznjaaufo.supabase.co/functions/v1/comp-worker"

def secret():
    s = os.environ.get("COMP_WORKER_SECRET")
    if s: return s.strip()
    return open(os.path.join(HERE, ".worker_secret")).read().strip()

def call(payload):
    payload = dict(payload, secret=secret())
    # curl (not urllib): macOS system python lacks CA certs
    r = subprocess.run(["curl", "-sS", "-m", "30", "-X", "POST", "-H", "Content-Type: application/json", "--data-binary", "@-", FN],
                       input=json.dumps(payload), text=True, capture_output=True)
    if r.returncode != 0:
        raise ConnectionError(r.stderr.strip()[:200])
    return json.loads(r.stdout)

def process_one():
    job = call({"action": "claim"}).get("job")
    if not job: return False
    label = "(hidden)" if os.environ.get("CI") else job["address"]   # keep addresses out of public CI logs
    print(time.strftime("%H:%M:%S"), "job", job["id"], label, flush=True)
    try:
        ex = dict(job.get("extra") or {}); ex["lessons"] = job.get("lessons") or []; ex["known_type"] = job.get("known_type")
        res = fast_comps.run(job["address"], ex)
    except Exception as e:
        res = {"ok": False, "error": repr(e)[:300]}
    call({"action": "complete", "id": job["id"], "ok": res["ok"], "data": res.get("data"), "error": res.get("error")})
    print(time.strftime("%H:%M:%S"), "job", job["id"], "done" if res["ok"] else "FAILED (details hidden in CI)" if os.environ.get("CI") else "FAILED " + str(res.get("error"))[:120], flush=True)
    return True

if __name__ == "__main__":
    if "--peek" in sys.argv:
        print("yes" if call({"action": "peek"}).get("queued") else "no"); sys.exit(0)
    once = "--once" in sys.argv
    while True:
        try:
            busy = process_one()
            if once and not busy: break
            if busy: continue
        except (ConnectionError, ValueError) as e:
            print("network:", e, flush=True)
            if once: break
        time.sleep(10)

import json, os, subprocess, sys, time, urllib.request, urllib.error
EP = os.environ.get("ENDPOINT") or "https://eduspark-quality.sites.tab.bot"
AUD = "eduspark-quality"
_tok = [None, 0]
def oidc():
    if _tok[0] and time.time() - _tok[1] < 240: return _tok[0]
    req = urllib.request.Request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=" + AUD, headers={"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    _tok[0] = json.load(urllib.request.urlopen(req, timeout=30))["value"]; _tok[1] = time.time()
    return _tok[0]
def call(method, path, body=None, data=None, tries=6):
    for a in range(tries):
        h = {"Authorization": "Bearer " + oidc(), "User-Agent": "lecture-copies"}
        payload = data
        if body is not None: payload = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        try:
            r = urllib.request.urlopen(urllib.request.Request(EP + path, data=payload, method=method, headers=h), timeout=600)
            return json.load(r)
        except urllib.error.HTTPError as e:
            txt = e.read().decode()[:300]; print("HTTP", e.code, path.split("?")[0], txt, flush=True)
            if e.code in (400, 401, 403, 404, 409, 415): return {"ok": False, "status": e.code, "words": txt}
        except Exception as e:
            print("retry", path.split("?")[0], e, flush=True)
        time.sleep(5 * (a + 1))
    return {"ok": False, "words": "no answer"}
def sh(cmd):
    print("+", " ".join(cmd)[:300], flush=True)
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
def upload(job, tok, q, path):
    size = os.path.getsize(path)
    s = call("POST", f"/api/jobs/start?job={job}&token={tok}&q={q}&size={size}")
    if not s.get("ok"): raise RuntimeError(f"start {q}: {s}")
    ps = s["partSize"]
    with open(path, "rb") as f:
        for n in range(1, s["parts"] + 1):
            f.seek((n - 1) * ps); b = f.read(ps)
            r = call("PUT", f"/api/jobs/part?job={job}&token={tok}&id={s['id']}&n={n}", data=b)
            if not r.get("ok"): raise RuntimeError(f"part {q} {n}: {r}")
    r = call("POST", f"/api/jobs/complete?job={job}&token={tok}&id={s['id']}")
    if not r.get("ok"): raise RuntimeError(f"complete {q}: {r}")
    print("uploaded", q, size, r.get("url"), flush=True)
def one(j):
    job, tok = j["job"], j["token"]
    os.makedirs("w", exist_ok=True)
    src = "w/src.mp4"
    sh(["curl", "-fsSL", "--retry", "5", "--retry-delay", "5", "-o", src, j["src"]])
    if os.path.getsize(src) != j["size"]: raise RuntimeError("download size mismatch")
    H = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", src]).splitlines()
    H = int(H[0]) if H and H[0].isdigit() else 1080
    audio = sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", src])
    spec = {"720p": (720, 23, "2200k", "4400k", "128k"), "480p": (480, 24, "1100k", "2200k", "96k"), "360p": (360, 26, "650k", "1300k", "64k")}
    outs = [o for o in j["outputs"] if o["q"] in spec]
    fc = f"[0:v]split={len(outs)}" + "".join(f"[s{i}]" for i in range(len(outs))) + ";" + ";".join(f"[s{i}]scale=-2:{min(H, spec[o['q']][0])}[v{i}]" for i, o in enumerate(outs))
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats", "-y", "-i", src, "-filter_complex", fc]
    for i, o in enumerate(outs):
        h, crf, mx, buf, ab = spec[o["q"]]
        cmd += ["-map", f"[v{i}]"] + (["-map", "0:a:0", "-c:a", "aac", "-ac", "2", "-b:a", ab] if audio else [])
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main", "-pix_fmt", "yuv420p", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-crf", str(crf), "-maxrate", mx, "-bufsize", buf, "-movflags", "+faststart", f"w/{o['q']}.mp4"]
    t = time.time(); subprocess.run(cmd, check=True); print("encoded in", round(time.time() - t), "s", flush=True)
    for o in outs: upload(job, tok, o["q"], f"w/{o['q']}.mp4")
    for f in os.listdir("w"): os.remove("w/" + f)
def main():
    t0 = time.time(); n = 0
    while time.time() - t0 < 4.5 * 3600:
        j = call("POST", "/api/jobs/claim", body={})
        if not j.get("ok") or not j.get("job"):
            print("no job" if j.get("ok") else f"claim failed: {j}"); break
        print("job", j["job"], j["size"], "bytes", flush=True)
        try:
            one(j); call("POST", f"/api/jobs/finish?job={j['job']}&token={j['token']}", body={"ok": True}); n += 1
            with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a") as f: f.write(f"copies made for {j['job']}\n")
        except Exception as e:
            print("FAILED", j["job"], e, flush=True)
            call("POST", f"/api/jobs/finish?job={j['job']}&token={j['token']}", body={"ok": False, "error": str(e)[:280]})
    print("done,", n, "job(s)")
main()

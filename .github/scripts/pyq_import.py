#!/usr/bin/env python3
# pyq_import.py <manifest.json> : see .github/workflows/pyq-import.yml
import json, os, re, subprocess, sys, time, zipfile, io, hashlib, tempfile, urllib.request
import pikepdf

MAN = sys.argv[1]
entries = json.load(open(MAN))
REPO = os.environ.get("GITHUB_REPOSITORY", "")
work = tempfile.mkdtemp()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def sh(*a, **k):
    return subprocess.run(list(a), capture_output=True, text=True, **k)

assets_cache = {}
def assets(tag):
    if tag not in assets_cache:
        r = sh("gh", "release", "view", tag, "--repo", REPO, "--json", "assets")
        if r.returncode != 0:
            title = tag
            for e in entries:
                if e["tag"] == tag and e.get("release_title"):
                    title = e["release_title"]; break
            c = sh("gh", "release", "create", tag, "--repo", REPO, "--title", title, "--notes",
                   "Previous year papers for the Eduspark app, copied from the official source named in each row.", "--latest=false")
            print("create release", tag, c.returncode, c.stderr[-300:])
            time.sleep(2)
            r = sh("gh", "release", "view", tag, "--repo", REPO, "--json", "assets")
        assets_cache[tag] = {a["name"]: a for a in json.loads(r.stdout or '{"assets":[]}')["assets"]}
    return assets_cache[tag]

dl_cache = {}
def download(url):
    if url in dl_cache and os.path.exists(dl_cache[url]):
        return dl_cache[url]
    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".bin"
    fn = os.path.join(work, hashlib.sha1(url.encode()).hexdigest() + ext)
    for i in range(5):
        r = sh("curl", "-fsSL", "--retry", "3", "--retry-delay", "5", "-m", "900", "-A", UA, "-o", fn, url)
        if r.returncode == 0 and os.path.getsize(fn) > 0:
            break
        print("download retry", i, url, r.stderr[-200:]); time.sleep(10 * (i + 1))
    else:
        raise RuntimeError("download failed: " + url)
    # keep the cache small: only the last 6 archives
    if len(dl_cache) > 6:
        old = list(dl_cache)[0]
        try: os.remove(dl_cache.pop(old))
        except Exception: pass
    dl_cache[url] = fn
    return fn

def extract(path, member):
    low = path.lower()
    if not member:
        return open(path, "rb").read()
    first, _, rest = member.partition("::")
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            data = z.read(first)
    else:
        r = subprocess.run(["bsdtar", "-xOf", path, first], capture_output=True)
        data = r.stdout
        if r.returncode != 0 or not data:
            d = tempfile.mkdtemp(dir=work)
            subprocess.run(["unar", "-q", "-o", d, path], capture_output=True)
            cand = os.path.join(d, first)
            if not os.path.exists(cand):
                for root, _, files in os.walk(d):
                    for f in files:
                        if f == os.path.basename(first):
                            cand = os.path.join(root, f)
            data = open(cand, "rb").read()
    if rest:
        with zipfile.ZipFile(io.BytesIO(data)) as z2:
            data = z2.read(rest)
    return data

BAD = re.compile(r"t\.me/|telegram|youtube|youtu\.be|whatsapp|instagram|facebook\.com|join (our|my) channel|subscribe", re.I)
URL = re.compile(r"(?:https?://|www\.)[^\s)>\]]+", re.I)
OKDOM = re.compile(r"cbse|nic\.in|gov\.in|ncert|nta\.ac\.in|jeeadv|iit|bseb|upmsp|jac\.jharkhand|biharboard|w3\.org|adobe|microsoft", re.I)

results = []
for e in entries:
    res = {"name": e["name"], "tag": e["tag"]}
    try:
        have = assets(e["tag"])
        if e["name"] in have and not e.get("replace"):
            res.update(status="exists", size=have[e["name"]]["size"]); results.append(res); continue
        data = extract(download(e["src"]), e.get("member"))
        if not data.startswith(b"%PDF"):
            i = data.find(b"%PDF")
            if 0 < i < 1024: data = data[i:]
            else: raise RuntimeError("not a PDF (starts %r)" % data[:8])
        src_pdf = os.path.join(work, "in.pdf"); out = os.path.join(work, e["name"])
        open(src_pdf, "wb").write(data)
        try:
            with pikepdf.open(src_pdf) as pdf:
                try:
                    del pdf.Root.Metadata
                except Exception:
                    pass
                info = pdf.docinfo
                for k in list(info.keys()):
                    del info[k]
                info["/Title"] = e.get("title") or e["name"]
                info["/Author"] = e.get("author", "CBSE")
                pages = len(pdf.pages)
                pdf.save(out)
        except Exception as ex:
            print("pikepdf failed, uploading the original bytes:", ex)
            open(out, "wb").write(data); pages = None
        txt = sh("pdftotext", "-layout", out, "-").stdout
        p1 = sh("pdftotext", "-l", "1", out, "-").stdout
        bad = sorted(set(m.group(0) for m in BAD.finditer(txt)))
        urls = sorted(set(u for u in URL.findall(txt) if not OKDOM.search(u)))
        if e.get("replace") and e["name"] in have:
            sh("gh", "release", "delete-asset", e["tag"], e["name"], "--repo", REPO, "-y")
        for i in range(4):
            u = sh("gh", "release", "upload", e["tag"], out, "--repo", REPO)
            if u.returncode == 0: break
            print("upload retry", e["name"], u.stderr[-300:]); time.sleep(8 * (i + 1))
        else:
            raise RuntimeError("upload failed: " + u.stderr[-300:])
        res.update(status="uploaded", size=os.path.getsize(out), pages=pages, page1=re.sub(r"\s+", " ", p1)[:500],
                   flags=bad, urls=urls[:20], chars=len(txt))
        os.remove(out)
    except Exception as ex:
        res.update(status="error", error=str(ex)[:500])
    print(json.dumps(res)[:400], flush=True)
    results.append(res)

os.makedirs("pyq-import/results", exist_ok=True)
rp = os.path.join("pyq-import/results", os.path.basename(MAN))
json.dump(results, open(rp, "w"), indent=1, ensure_ascii=False)
sh("git", "config", "user.name", "pyq-import"); sh("git", "config", "user.email", "actions@users.noreply.github.com")
for i in range(6):
    sh("git", "add", rp); sh("git", "commit", "-m", "pyq-import report " + os.path.basename(MAN))
    p = sh("git", "push")
    if p.returncode == 0: break
    sh("git", "pull", "--rebase", "-X", "theirs"); time.sleep(3 + i * 3)
print("done", sum(r["status"] == "uploaded" for r in results), "uploaded,", sum(r["status"] == "exists" for r in results), "existed,",
      sum(r["status"] == "error" for r in results), "errors")

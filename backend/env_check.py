import os, re, json, time, urllib.request
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
print("File the backend reads:", path)
print("Exists:", os.path.exists(path))
if os.path.exists(path):
    print("Last saved:", time.ctime(os.path.getmtime(path)))
    names = ("LLM_PROVIDER", "LLM_MODEL", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "TINYFISH_API_KEY")
    for n, line in enumerate(open(path, encoding="utf-8-sig"), 1):
        m = re.match(r"\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*)", line)
        if m and m.group(1) in names:
            v = m.group(2).split(" #")[0].strip().strip("\"'")
            shown = v if m.group(1) in ("LLM_PROVIDER", "LLM_MODEL") else ("<has value>" if v else "<empty>")
            print(f"  line {n}: {m.group(1)} = {shown}")
for k in ("LLM_PROVIDER", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
    print(f"System environment {k}:", "SET" if os.environ.get(k) else "not set")
try:
    s = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/settings", timeout=3))
    print("Running backend says provider:", s["llm"]["provider"], "| backend folder:", s.get("backend_process", {}).get("folder", "?"))
except Exception as e:
    print("Running backend: not reachable", e)
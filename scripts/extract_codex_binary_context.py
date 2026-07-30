from pathlib import Path


EXE = Path(r"C:\Program Files\WindowsApps\OpenAI.Codex_26.519.2081.0_x64__2p2nqsd0c76g0\app\resources\codex.exe")
TERMS = [
    b"thread/list",
    b"sourceKinds",
    b"useStateDbOnly",
    b"has_user_event",
    b"thread_source",
    b"threads.cwd",
    b"preview <>",
    b"source IN",
    b"WHERE 1 = 1",
    b"ThreadListParams",
    b"thread-workspace-root-hints",
    b"session_index",
    b"electron-saved-workspace-roots",
    b"project-order",
    b"active-workspace-roots",
]


def show_context(data: bytes, term: bytes, limit: int = 12):
    print(f"== {term.decode('ascii', 'replace')} ==")
    start = 0
    seen = 0
    while seen < limit:
        idx = data.find(term, start)
        if idx < 0:
            break
        lo = max(0, idx - 900)
        hi = min(len(data), idx + 1600)
        chunk = data[lo:hi]
        text = chunk.decode("utf-8", "replace")
        text = "".join(ch if ch == "\n" or ch == "\r" or 32 <= ord(ch) < 127 else " " for ch in text)
        text = " ".join(text.split())
        print(f"-- offset {idx} --")
        print(text[:2500])
        start = idx + len(term)
        seen += 1
    if seen == 0:
        print("(no matches)")


def main():
    print({"path": str(EXE), "exists": EXE.exists(), "size": EXE.stat().st_size if EXE.exists() else None})
    data = EXE.read_bytes()
    for term in TERMS:
        show_context(data, term)


if __name__ == "__main__":
    main()

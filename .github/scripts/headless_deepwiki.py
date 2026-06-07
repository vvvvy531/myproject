#!/usr/bin/env python3
"""Headless DeepWiki generator.

This script drives deepwiki-open without the Next.js frontend.
It uses the FastAPI REST and WebSocket endpoints directly.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import websocket


TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".cs", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yml", ".yaml",
    ".ini", ".uproject", ".uplugin", ".md", ".txt", ".sh", ".bat", ".ps1",
    ".cmake", ".target", ".build", ".rs", ".go", ".java", ".kt", ".swift",
}

EXCLUDED_PARTS = {
    ".git", ".github", ".vs", ".vscode", "Binaries", "Build", "DerivedDataCache",
    "Intermediate", "Saved", "node_modules", "vendor", "vendors", "third_party",
    "ThirdParty", "External", "Externals", "Libraries", "Library", "SDK", "SDKs",
    "__pycache__", ".venv", "venv", "dist",
}


def normalize_openai_base_url() -> str:
    base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("REVIEW_PROXY_URL", "")
    if not base:
        raise RuntimeError("OPENAI_BASE_URL or REVIEW_PROXY_URL is required for direct generation fallback")
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def extract_text_from_response(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    texts: list[str] = []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                texts.append(value["text"])
            elif isinstance(value.get("content"), str):
                texts.append(value["content"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    if "output" in payload:
        walk(payload["output"])
    if not texts and payload.get("choices"):
        walk(payload["choices"])
    return "\n".join(t for t in texts if t).strip()


def extract_text_from_http_response(response: requests.Response) -> str:
    raw = response.text or ""
    try:
        return extract_text_from_response(response.json())
    except Exception:
        pass

    texts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            text = extract_text_from_response(json.loads(data))
            if text:
                texts.append(text)
        except Exception:
            if data and not data.startswith("{"):
                texts.append(data)
    if texts:
        return "\n".join(texts).strip()

    clean = raw.strip()
    if clean and not clean.lower().startswith(("<html", "<!doctype html")):
        return clean
    return ""


def call_remote_model(prompt: str, model: str, timeout: int = 180) -> str:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("REVIEW_PROXY_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY or REVIEW_PROXY_KEY is required for direct generation fallback")
    base_url = normalize_openai_base_url()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    attempts: list[str] = []
    endpoints = [
        ("/responses", {"model": model, "input": prompt, "stream": False}),
        ("/chat/completions", {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "stream": False}),
    ]
    for attempt in range(1, 4):
        for endpoint, payload in endpoints:
            try:
                response = requests.post(f"{base_url}{endpoint}", headers=headers, json=payload, timeout=timeout)
                if response.ok:
                    text = extract_text_from_http_response(response)
                    if text:
                        return text
                    attempts.append(f"{endpoint} attempt={attempt} HTTP {response.status_code} empty/non-text body len={len(response.text or '')}")
                else:
                    body = re.sub(r"\s+", " ", response.text or "")[:240]
                    attempts.append(f"{endpoint} attempt={attempt} HTTP {response.status_code}: {body}")
            except Exception as exc:
                attempts.append(f"{endpoint} attempt={attempt} {type(exc).__name__}: {exc}")
        time.sleep(2 * attempt)
    raise RuntimeError("Remote model request failed: " + " | ".join(attempts[-8:]))

def should_include_file(path: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.suffix and path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        return path.is_file() and path.stat().st_size <= 200_000
    except OSError:
        return False


def collect_source_context(repo_path: str, file_paths: list[str] | None = None, max_files: int = 24, max_chars: int = 80_000) -> str:
    root = Path(repo_path)
    if not repo_path or not root.exists():
        return ""

    candidates: list[Path] = []
    if file_paths:
        for rel in file_paths:
            rel_path = Path(rel)
            path = root / rel_path
            if should_include_file(path):
                candidates.append(path)
    if not candidates:
        candidates = [p for p in root.rglob("*") if should_include_file(p)]
        candidates.sort(key=lambda p: (len(p.parts), str(p).lower()))

    chunks: list[str] = []
    used = 0
    for path in candidates[:max_files]:
        try:
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8", errors="ignore")[:6000]
        except Exception:
            continue
        block = f"<file path=\"{rel}\">\n{text}\n</file>"
        if used + len(block) > max_chars:
            break
        chunks.append(block)
        used += len(block)
    return "\n\n".join(chunks)


def fallback_structure(display_name: str, file_tree: str, is_comprehensive: bool) -> dict:
    paths = []
    for raw in file_tree.splitlines():
        line = raw.strip().lstrip("-├─└│ ").strip()
        if not line or line.endswith("/"):
            continue
        if any(part in EXCLUDED_PARTS for part in Path(line).parts):
            continue
        suffix = Path(line).suffix.lower()
        if not suffix or suffix in TEXT_SUFFIXES:
            paths.append(line)
    paths = list(dict.fromkeys(paths))[:40]
    page_defs = [
        ("overview", "Project Overview", paths[:8]),
        ("architecture", "Architecture", paths[2:14] or paths[:8]),
        ("configuration", "Configuration", [p for p in paths if Path(p).suffix.lower() in {".json", ".yml", ".yaml", ".ini", ".uproject", ".uplugin"}][:10] or paths[:6]),
        ("development-workflow", "Development Workflow", paths[:10]),
    ]
    if is_comprehensive:
        page_defs.extend([
            ("key-modules", "Key Modules", paths[4:20] or paths[:10]),
            ("runtime-behavior", "Runtime Behavior", paths[6:22] or paths[:10]),
        ])
    return {
        "title": f"{display_name} Wiki",
        "description": "Generated DeepWiki-style documentation.",
        "pages": [
            {
                "id": page_id,
                "title": title,
                "importance": "high" if idx == 0 else "medium",
                "filePaths": files,
                "relatedPages": [other_id for other_id, _, _ in page_defs if other_id != page_id][:3],
                "content": "",
            }
            for idx, (page_id, title, files) in enumerate(page_defs)
        ],
    }


def generate_structure_direct(repo_path: str, display_name: str, file_tree: str, model: str, language: str, is_comprehensive: bool) -> dict:
    page_count = "8-12" if is_comprehensive else "4-6"
    source_context = collect_source_context(repo_path, max_files=32, max_chars=90_000)
    prompt = (
        f"Create a DeepWiki-style wiki structure for repository {display_name}.\n"
        f"Language: {language}.\n"
        f"Create {page_count} pages. Use stable page ids such as overview, architecture, workflow.\n\n"
        f"File tree:\n<file_tree>\n{file_tree}\n</file_tree>\n\n"
        f"Source excerpts:\n<source_context>\n{source_context}\n</source_context>\n\n"
        "Return ONLY this XML, no markdown fences:\n"
        "<wiki_structure>\n"
        "  <title>...</title>\n"
        "  <description>...</description>\n"
        "  <page id=\"overview\">\n"
        "    <title>...</title>\n"
        "    <importance>high</importance>\n"
        "    <file_path>relative/path</file_path>\n"
        "    <related>another-page-id</related>\n"
        "  </page>\n"
        "</wiki_structure>"
    )
    try:
        text = call_remote_model(prompt, model)
        if os.environ.get("DEEPWIKI_DEBUG_RESPONSE") == "true" and "<wiki_structure" not in text:
            print(f"Direct structure response preview: {response_preview(text)}", file=sys.stderr)
        return parse_wiki_structure_xml(text)
    except Exception as exc:
        print(f"[deepwiki] direct structure XML unavailable; using deterministic structure: {exc}", file=sys.stderr)
        return fallback_structure(display_name, file_tree, is_comprehensive)


def fallback_page_content(page: dict) -> str:
    title = page.get("title") or page.get("id") or "Wiki Page"
    file_paths = page.get("filePaths", [])
    files = "\n".join(f"- `{path}`" for path in file_paths) or "- No specific files selected."
    return (
        f"# {title}\n\n"
        "This page was generated as a safe fallback because the remote model response was unavailable for this page.\n\n"
        "## Relevant Files\n\n"
        f"{files}\n\n"
        "## Notes\n\n"
        "Review the listed files in the private repository for implementation details.\n"
    )


def generate_page_content_direct(repo_path: str, page: dict, model: str, language: str) -> str:
    source_context = collect_source_context(repo_path, page.get("filePaths", []), max_files=12, max_chars=45_000)
    file_list = "\n".join(f"- {path}" for path in page.get("filePaths", []))
    prompt = (
        "Generate one DeepWiki-style wiki page in Markdown.\n"
        f"Language: {language}.\n"
        f"Topic: {page.get('title', page.get('id', 'Wiki Page'))}\n"
        f"Relevant files:\n{file_list}\n\n"
        f"Source excerpts:\n<source_context>\n{source_context}\n</source_context>\n\n"
        "Requirements: concise technical explanation, headings, bullet points, and Mermaid diagram if useful. "
        "Return only Markdown."
    )
    try:
        text = call_remote_model(prompt, model).strip()
        return text or fallback_page_content(page)
    except Exception as exc:
        print(f"[deepwiki] direct page fallback used for {page.get('id', 'page')}: {exc}", file=sys.stderr)
        return fallback_page_content(page)

def wait_for_health(base_url: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=5)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(2)
    raise SystemExit(f"DeepWiki API did not become healthy within {timeout}s")


def fetch_repo_structure(base_url: str, repo_path: str, owner: str, repo: str, repo_type: str) -> dict:
    if repo_path:
        response = requests.get(
            f"{base_url}/local_repo/structure",
            params={"path": repo_path},
            timeout=120,
        )
    else:
        response = requests.get(
            f"{base_url}/repo/structure",
            params={"owner": owner, "repo": repo, "type": repo_type},
            timeout=120,
        )
    response.raise_for_status()
    return response.json()


def send_websocket_request(ws_url: str, payload: dict) -> str:
    collected: list[str] = []

    def on_open(ws):
        ws.send(json.dumps(payload))

    def on_message(ws, message):
        collected.append(message)

    def on_error(ws, error):
        print(f"WebSocket error: {error}", file=sys.stderr)

    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)
    return "".join(collected)


def response_preview(text: str, limit: int = 800) -> str:
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:limit]


def parse_wiki_structure_xml(text: str) -> dict:
    match = re.search(r"<wiki_structure>[\s\S]*?</wiki_structure>", text)
    if not match:
        if os.environ.get("DEEPWIKI_DEBUG_RESPONSE") == "true":
            print(f"DeepWiki structure response preview: {response_preview(text)}", file=sys.stderr)
        raise ValueError("No <wiki_structure> block found in response")
    xml_text = match.group(0)
    xml_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", xml_text)

    title_match = re.search(r"<title>(.*?)</title>", xml_text, re.S)
    description_match = re.search(r"<description>(.*?)</description>", xml_text, re.S)
    pages: list[dict] = []
    for page_match in re.finditer(r"<page id=\"([^\"]+)\">([\s\S]*?)</page>", xml_text):
        page_id = page_match.group(1)
        body = page_match.group(2)
        page_title_match = re.search(r"<title>(.*?)</title>", body, re.S)
        importance_match = re.search(r"<importance>(.*?)</importance>", body, re.S)
        file_paths = re.findall(r"<file_path>(.*?)</file_path>", body)
        related = re.findall(r"<related>(.*?)</related>", body)
        pages.append(
            {
                "id": page_id,
                "title": page_title_match.group(1) if page_title_match else page_id,
                "importance": importance_match.group(1) if importance_match else "medium",
                "filePaths": file_paths,
                "relatedPages": related,
                "content": "",
            }
        )

    return {
        "title": title_match.group(1) if title_match else "Wiki",
        "description": description_match.group(1) if description_match else "",
        "pages": pages,
    }


def generate_structure(
    ws_url: str,
    repo_url: str,
    repo_type: str,
    display_name: str,
    file_tree: str,
    provider: str,
    model: str,
    language: str,
    token: str,
    is_comprehensive: bool,
) -> dict:
    page_count = "8-12" if is_comprehensive else "4-6"
    prompt = (
        f"Analyze this repository {display_name} and create a wiki structure for it.\n\n"
        f"Complete file tree:\n<file_tree>\n{file_tree}\n</file_tree>\n\n"
        f"Create {page_count} pages covering project overview, architecture, key features, "
        f"configuration, development workflow, and critical modules.\n\n"
        "Return ONLY this XML format, no markdown fences, no explanation:\n"
        "<wiki_structure>\n"
        "  <title>...</title>\n"
        "  <description>...</description>\n"
        "  <pages>\n"
        "    <page id=\"page-1\">\n"
        "      <title>...</title>\n"
        "      <description>...</description>\n"
        "      <importance>high</importance>\n"
        "      <relevant_files><file_path>README.md</file_path></relevant_files>\n"
        "      <related_pages><related>page-2</related></related_pages>\n"
        "    </page>\n"
        "  </pages>\n"
        "</wiki_structure>"
    )
    payload = {
        "repo_url": repo_url,
        "type": repo_type,
        "messages": [{"role": "user", "content": prompt}],
        "provider": provider,
        "model": model,
        "language": language,
    }
    if token:
        payload["token"] = token
    response_text = send_websocket_request(ws_url, payload)
    return parse_wiki_structure_xml(response_text)


def generate_page_content(
    ws_url: str,
    repo_url: str,
    repo_type: str,
    page: dict,
    provider: str,
    model: str,
    language: str,
    token: str,
) -> str:
    file_list = "\n".join(f"- {path}" for path in page.get("filePaths", []))
    prompt = (
        f"Generate a comprehensive wiki page in Markdown format.\n"
        f"Topic: {page['title']}\n"
        f"Relevant source files:\n{file_list}\n\n"
        f"Use clear headings, concise technical explanations, and Mermaid diagrams where useful."
    )
    payload = {
        "repo_url": repo_url,
        "type": repo_type,
        "messages": [{"role": "user", "content": prompt}],
        "provider": provider,
        "model": model,
        "language": language,
    }
    if token:
        payload["token"] = token
    return send_websocket_request(ws_url, payload)


def write_wiki_output(structure: dict, pages: dict[str, str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_lines = [f"# {structure.get('title', 'Wiki')}", "", structure.get("description", ""), ""]
    for page in structure.get("pages", []):
        page_id = page["id"]
        content = pages.get(page_id, "")
        if not content:
            continue
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", page_id).strip("-") or page_id
        page_path = output_dir / f"{safe_name}.md"
        page_path.write_text(content, encoding="utf-8")
        index_lines.append(f"- [{page['title']}]({page_path.name})")

    (output_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless DeepWiki generator")
    parser.add_argument("--base-url", default=os.environ.get("DEEPWIKI_BASE_URL", "http://localhost:8001"))
    parser.add_argument("--owner", default=os.environ.get("DEEPWIKI_OWNER", "local"))
    parser.add_argument("--repo", default=os.environ.get("DEEPWIKI_REPO", "repo"))
    parser.add_argument("--repo-path", default=os.environ.get("DEEPWIKI_REPO_PATH", ""))
    parser.add_argument("--repo-type", default=os.environ.get("DEEPWIKI_REPO_TYPE", "github"))
    parser.add_argument("--provider", default=os.environ.get("DEEPWIKI_PROVIDER", "openai"))
    parser.add_argument("--model", default=os.environ.get("DEEPWIKI_MODEL", "gpt-4o"))
    parser.add_argument("--language", default=os.environ.get("DEEPWIKI_LANGUAGE", "en"))
    parser.add_argument("--token", default=os.environ.get("DEEPWIKI_TOKEN", ""))
    parser.add_argument("--output-dir", default=os.environ.get("DEEPWIKI_OUTPUT_DIR", "wiki-output"))
    parser.add_argument("--health-timeout", type=int, default=300)
    parser.add_argument("--comprehensive", action="store_true")
    args = parser.parse_args()

    wait_for_health(args.base_url, args.health_timeout)

    structure_payload = fetch_repo_structure(args.base_url, args.repo_path, args.owner, args.repo, args.repo_type)
    file_tree = structure_payload.get("file_tree") or structure_payload.get("fileTree", "")
    if not file_tree:
        raise SystemExit("Empty repository file tree returned")

    repo_url = args.repo_path if args.repo_path else f"https://github.com/{args.owner}/{args.repo}"
    display_name = f"{args.owner}/{args.repo}"
    ws_url = args.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/chat"

    print("[deepwiki] generating wiki structure")
    use_direct_pages = False
    try:
        structure = generate_structure(
            ws_url,
            repo_url,
            args.repo_type,
            display_name,
            file_tree,
            args.provider,
            args.model,
            args.language,
            args.token,
            args.comprehensive,
        )
    except Exception as exc:
        print(f"[deepwiki] DeepWiki WebSocket generation unavailable; using direct remote model fallback: {exc}", file=sys.stderr)
        structure = generate_structure_direct(args.repo_path, display_name, file_tree, args.model, args.language, args.comprehensive)
        use_direct_pages = True
    print(f"[deepwiki] generated {len(structure.get('pages', []))} pages")

    page_contents: dict[str, str] = {}
    pages_list = structure.get("pages", [])
    for idx, page in enumerate(pages_list, start=1):
        print(f"[deepwiki] generating page {idx}/{len(pages_list)}")
        if use_direct_pages:
            content = generate_page_content_direct(args.repo_path, page, args.model, args.language)
        else:
            content = generate_page_content(
                ws_url,
                repo_url,
                args.repo_type,
                page,
                args.provider,
                args.model,
                args.language,
                args.token,
            )
            if not content.strip() or content.lstrip().startswith("Error:") or "No valid document embeddings" in content:
                content = generate_page_content_direct(args.repo_path, page, args.model, args.language)
        page_contents[page["id"]] = content

    output_dir = Path(args.output_dir)
    write_wiki_output(structure, page_contents, output_dir)
    print(f"[deepwiki] wrote wiki output to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

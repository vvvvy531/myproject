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


def parse_wiki_structure_xml(text: str) -> dict:
    match = re.search(r"<wiki_structure>[\s\S]*?</wiki_structure>", text)
    if not match:
        preview = text[:4000].replace(os.environ.get("OPENAI_API_KEY", ""), "***")
        print("No <wiki_structure> block found in response. Response preview:", file=sys.stderr)
        print(preview, file=sys.stderr)
        raise SystemExit("No <wiki_structure> block found in response")
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
    print(f"[deepwiki] generated {len(structure.get('pages', []))} pages")

    page_contents: dict[str, str] = {}
    for page in structure.get("pages", []):
        print(f"[deepwiki] generating page: {page['title']}")
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
        page_contents[page["id"]] = content

    output_dir = Path(args.output_dir)
    write_wiki_output(structure, page_contents, output_dir)
    print(f"[deepwiki] wrote wiki output to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
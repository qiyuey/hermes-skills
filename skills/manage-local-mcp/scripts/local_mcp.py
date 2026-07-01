#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


NAMESPACE = "top.qiyuey"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8931
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        die("server name must contain only letters, numbers, '_' or '-'")
    return name


def label_for(name: str, override: str | None = None) -> str:
    if override:
        return override
    return f"{NAMESPACE}.{safe_name(name)}-mcp"


def launch_domain() -> str:
    return f"gui/{os.getuid()}"


def launch_service_target(label: str) -> str:
    return f"{launch_domain()}/{label}"


def launch_agent_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d%H%M%S")


def run(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not quiet and proc.stdout:
        print(proc.stdout, end="")
    if not quiet and proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc


def require_macos() -> None:
    if sys.platform != "darwin":
        die("LaunchAgent service management is only implemented for macOS")


def parse_env_pairs(pairs: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            die(f"invalid --env value: {pair!r}; expected KEY=VALUE")
        env[key] = value
    return env


def build_program_args(args: argparse.Namespace) -> list[str]:
    if args.playwright_edge:
        command = "exec npx @playwright/mcp@latest --port {port} --host {host} --extension --browser=msedge".format(
            port=shlex.quote(str(args.port)),
            host=shlex.quote(args.host),
        )
        return ["/bin/sh", "-lc", command]
    if args.shell_command:
        return ["/bin/sh", "-lc", f"exec {args.shell_command}"]
    if args.command:
        return [args.command, *(args.arg or [])]
    die("provide --playwright-edge, --shell-command, or --command/--arg")


def install_service(args: argparse.Namespace) -> None:
    require_macos()
    name = safe_name(args.name)
    label = label_for(name, args.label)
    plist_path = launch_agent_path(label)
    if plist_path.exists() and not args.force:
        die(f"{plist_path} already exists; pass --force to replace it")

    stdout = args.stdout or f"/tmp/{name}-mcp-{args.port}.log"
    stderr = args.stderr or f"/tmp/{name}-mcp-{args.port}.err.log"
    env = {"PATH": PATH_ENV}
    env.update(parse_env_pairs(args.env))
    plist = {
        "Label": label,
        "ProgramArguments": build_program_args(args),
        "RunAtLoad": True,
        "KeepAlive": not args.no_keepalive,
        "WorkingDirectory": str(Path.home()),
        "StandardOutPath": stdout,
        "StandardErrorPath": stderr,
        "EnvironmentVariables": env,
    }

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.exists():
        backup = plist_path.with_name(f"{plist_path.name}.bak-local-mcp-{timestamp()}")
        shutil.copy2(plist_path, backup)
        print(f"backed up {plist_path} -> {backup}")
        run(["launchctl", "bootout", launch_domain(), str(plist_path)], quiet=True)
        run(["launchctl", "bootout", launch_service_target(label)], quiet=True)

    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)

    proc = run(["launchctl", "bootstrap", launch_domain(), str(plist_path)], quiet=True)
    if proc.returncode != 0:
        die(proc.stderr.strip() or f"launchctl bootstrap failed for {plist_path}")
    run(["launchctl", "kickstart", "-k", launch_service_target(label)], quiet=True)
    print(f"installed {label}")
    print(f"plist: {plist_path}")
    print(f"logs: {stdout}, {stderr}")


def uninstall_service(args: argparse.Namespace) -> None:
    require_macos()
    name = safe_name(args.name)
    label = label_for(name, args.label)
    plist_path = launch_agent_path(label)
    run(["launchctl", "bootout", launch_domain(), str(plist_path)], quiet=True)
    run(["launchctl", "bootout", launch_service_target(label)], quiet=True)
    if plist_path.exists():
        if args.keep_plist:
            print(f"left plist in place: {plist_path}")
        else:
            plist_path.unlink()
            print(f"removed {plist_path}")
    else:
        print(f"plist not found: {plist_path}")

    if args.kill_port:
        kill_processes_on_port(args.port)


def kill_processes_on_port(port: int) -> None:
    proc = run(["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"], quiet=True)
    pids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not pids:
        print(f"no listener found on port {port}")
        return
    for pid in pids:
        run(["kill", pid], quiet=True)
        print(f"killed pid {pid} on port {port}")


def socket_open(host: str, port: int, timeout_seconds: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def probe_mcp(url: str, timeout_seconds: float = 3.0) -> tuple[bool, str]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "local-mcp-doctor", "version": "0.1.0"},
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(4096).decode("utf-8", "replace")
            return True, f"HTTP {response.status}: {body[:300]}"
    except urllib.error.HTTPError as exc:
        body = exc.read(1024).decode("utf-8", "replace")
        return False, f"HTTP {exc.code}: {body[:300]}"
    except Exception as exc:
        return False, str(exc)


def status_service(args: argparse.Namespace) -> None:
    name = safe_name(args.name)
    label = label_for(name, args.label)
    plist_path = launch_agent_path(label)
    print(f"name: {name}")
    print(f"label: {label}")
    print(f"plist: {plist_path} ({'present' if plist_path.exists() else 'missing'})")

    if sys.platform == "darwin":
        proc = run(["launchctl", "print", launch_service_target(label)], quiet=True)
        print(f"launchctl: {'loaded' if proc.returncode == 0 else 'not loaded'}")
    else:
        print("launchctl: unavailable on this OS")

    host = args.host or DEFAULT_HOST
    if args.port:
        print(f"tcp {host}:{args.port}: {'open' if socket_open(host, args.port) else 'closed'}")
        proc = run(["lsof", "-nP", f"-iTCP:{args.port}", "-sTCP:LISTEN"], quiet=True)
        if proc.returncode == 0 and proc.stdout.strip():
            print(proc.stdout.rstrip())

    if args.url:
        ok, detail = probe_mcp(args.url)
        print(f"mcp initialize: {'ok' if ok else 'failed'}")
        print(detail)


def doctor(args: argparse.Namespace) -> None:
    status_service(args)
    print()
    for client in parse_clients(args.clients):
        path = client_path(args, client)
        present = path.exists()
        marker = config_marker(client, args.name, args.url)
        contains = present and marker in path.read_text(encoding="utf-8", errors="replace")
        print(f"{client}: {path} ({'present' if present else 'missing'}, {'configured' if contains else 'not confirmed'})")


def parse_clients(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return ["codex", "zed", "hermes"]
    clients = [item.strip().lower() for item in value.split(",") if item.strip()]
    allowed = {"codex", "zed", "hermes"}
    unknown = sorted(set(clients) - allowed)
    if unknown:
        die(f"unknown client(s): {', '.join(unknown)}")
    return clients


def client_path(args: argparse.Namespace, client: str) -> Path:
    if client == "codex":
        return args.codex_config or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    if client == "zed":
        return args.zed_config or Path.home() / ".config" / "zed" / "settings.json"
    if client == "hermes":
        return args.hermes_config or Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"
    die(f"unsupported client: {client}")


def config_marker(client: str, name: str, url: str | None) -> str:
    if client == "codex":
        return f"[mcp_servers.{name}]"
    if client in {"zed", "hermes"}:
        return url or name
    return name


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str, *, dry_run: bool) -> None:
    old = read_text(path)
    if old == text:
        print(f"unchanged {path}")
        return
    if dry_run:
        print(f"would update {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-local-mcp-{timestamp()}")
        shutil.copy2(path, backup)
        print(f"backed up {path} -> {backup}")
    path.write_text(text, encoding="utf-8")
    print(f"updated {path}")


def remove_codex_block(text: str, name: str) -> str:
    pattern = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    new_text, count = pattern.subn("", text)
    if count == 0:
        return text
    new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip()
    return new_text + ("\n" if new_text else "")


def configure_codex(path: Path, name: str, url: str, *, dry_run: bool) -> None:
    text = read_text(path)
    text = remove_codex_block(text, name).rstrip()
    block = f"[mcp_servers.{name}]\nurl = {json.dumps(url)}\n"
    new_text = f"{text}\n\n{block}" if text else block
    write_text(path, new_text, dry_run=dry_run)


def remove_config_codex(path: Path, name: str, *, dry_run: bool) -> None:
    text = read_text(path)
    new_text = remove_codex_block(text, name)
    write_text(path, new_text, dry_run=dry_run)


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    in_string = False
    escape = False
    line_comment = False
    block_comment = False
    i = open_index
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if line_comment:
            if ch == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    die("could not find matching JSON object brace")


def find_object_for_key(text: str, key: str, start: int = 0, end: int | None = None) -> tuple[int, int, int, int] | None:
    end = len(text) if end is None else end
    pattern = re.compile(rf'{re.escape(json.dumps(key))}\s*:\s*\{{')
    match = pattern.search(text, start, end)
    if not match:
        return None
    open_index = match.end() - 1
    close_index = find_matching_brace(text, open_index)
    return match.start(), open_index, close_index, match.end()


def line_indent_before(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    line = text[line_start:index]
    return re.match(r"\s*", line).group(0)


def zed_server_snippet(name: str, url: str, indent: str) -> str:
    child = indent + "  "
    return (
        f'{indent}{json.dumps(name)}: {{\n'
        f'{child}"enabled": true,\n'
        f'{child}"url": {json.dumps(url)}\n'
        f"{indent}}}"
    )


def insert_jsonc_property(text: str, obj_open: int, obj_close: int, snippet: str, base_indent: str) -> str:
    body = text[obj_open + 1 : obj_close]
    if not body.strip():
        return text[: obj_open + 1] + "\n" + snippet + "\n" + base_indent + text[obj_close:]
    return text[: obj_open + 1] + "\n" + snippet + "," + text[obj_open + 1 :]


def replace_jsonc_property(text: str, key_start: int, obj_close: int, snippet: str) -> str:
    return text[:key_start] + snippet.lstrip() + text[obj_close + 1 :]


def remove_jsonc_property(text: str, key_start: int, obj_close: int) -> str:
    line_start = text.rfind("\n", 0, key_start) + 1
    end = obj_close + 1
    while end < len(text) and text[end] in " \t":
        end += 1
    if end < len(text) and text[end] == ",":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    new_text = text[:line_start] + text[end:]
    new_text = re.sub(r",(\s*[}\]])", r"\1", new_text)
    new_text = re.sub(r"({|\[)\s*,", r"\1", new_text)
    return new_text


def configure_zed(path: Path, name: str, url: str, *, dry_run: bool) -> None:
    text = read_text(path)
    if not text.strip():
        text = "{\n}"
    context = find_object_for_key(text, "context_servers")
    if context is None:
        top_open = text.find("{")
        if top_open < 0:
            text = "{\n}"
            top_open = 0
        top_close = find_matching_brace(text, top_open)
        base_indent = line_indent_before(text, top_open)
        prop_indent = base_indent + "  "
        snippet = (
            f'{prop_indent}"context_servers": {{\n'
            f'{zed_server_snippet(name, url, prop_indent + "  ")}\n'
            f"{prop_indent}}}"
        )
        new_text = insert_jsonc_property(text, top_open, top_close, snippet, base_indent)
        write_text(path, new_text, dry_run=dry_run)
        return

    key_start, obj_open, obj_close, _ = context
    base_indent = line_indent_before(text, key_start)
    server_indent = base_indent + "  "
    server = find_object_for_key(text, name, obj_open + 1, obj_close)
    snippet = zed_server_snippet(name, url, server_indent)
    if server:
        server_key_start, _, server_obj_close, _ = server
        new_text = replace_jsonc_property(text, server_key_start, server_obj_close, snippet)
    else:
        new_text = insert_jsonc_property(text, obj_open, obj_close, snippet, base_indent)
    write_text(path, new_text, dry_run=dry_run)


def remove_config_zed(path: Path, name: str, *, dry_run: bool) -> None:
    text = read_text(path)
    context = find_object_for_key(text, "context_servers")
    if not context:
        print(f"not configured {path}")
        return
    _, obj_open, obj_close, _ = context
    server = find_object_for_key(text, name, obj_open + 1, obj_close)
    if not server:
        print(f"not configured {path}")
        return
    server_key_start, _, server_obj_close, _ = server
    write_text(path, remove_jsonc_property(text, server_key_start, server_obj_close), dry_run=dry_run)


def hermes_block(name: str, url: str | None = None, *, enabled: bool = True) -> list[str]:
    if url is None:
        return [f"  {name}:\n", f"    enabled: {'true' if enabled else 'false'}\n"]
    return [
        f"  {name}:\n",
        f"    url: {json.dumps(url)}\n",
        f"    enabled: {'true' if enabled else 'false'}\n",
    ]


def find_hermes_section(lines: list[str]) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        if re.fullmatch(r"mcp_servers:\s*(#.*)?\n?", line):
            end = len(lines)
            for cursor in range(index + 1, len(lines)):
                if lines[cursor].strip() and not lines[cursor].startswith((" ", "\t", "#")):
                    end = cursor
                    break
            return index, end
    return None


def find_hermes_server(lines: list[str], start: int, end: int, name: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"^  {re.escape(name)}:\s*(#.*)?$")
    for index in range(start + 1, end):
        if pattern.match(lines[index].rstrip("\n")):
            block_end = end
            for cursor in range(index + 1, end):
                if re.match(r"^  [A-Za-z0-9_-]+:\s*(#.*)?$", lines[cursor].rstrip("\n")):
                    block_end = cursor
                    break
            return index, block_end
    return None


def configure_hermes(path: Path, name: str, url: str, *, dry_run: bool) -> None:
    text = read_text(path)
    lines = text.splitlines(keepends=True)
    block = hermes_block(name, url, enabled=True)
    section = find_hermes_section(lines)
    if section is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend(["mcp_servers:\n", *block])
    else:
        start, end = section
        server = find_hermes_server(lines, start, end, name)
        if server:
            server_start, server_end = server
            lines[server_start:server_end] = block
        else:
            lines[start + 1 : start + 1] = block
    write_text(path, "".join(lines), dry_run=dry_run)


def remove_config_hermes(path: Path, name: str, *, dry_run: bool, delete: bool) -> None:
    text = read_text(path)
    if not text:
        print(f"not configured {path}")
        return
    lines = text.splitlines(keepends=True)
    section = find_hermes_section(lines)
    if not section:
        print(f"not configured {path}")
        return
    server = find_hermes_server(lines, section[0], section[1], name)
    if not server:
        print(f"not configured {path}")
        return
    start, end = server
    lines[start:end] = [] if delete else hermes_block(name, enabled=False)
    write_text(path, "".join(lines), dry_run=dry_run)


def configure(args: argparse.Namespace) -> None:
    name = safe_name(args.name)
    if not args.url:
        die("--url is required for configure")
    for client in parse_clients(args.clients):
        path = client_path(args, client)
        if client == "codex":
            configure_codex(path, name, args.url, dry_run=args.dry_run)
        elif client == "zed":
            configure_zed(path, name, args.url, dry_run=args.dry_run)
        elif client == "hermes":
            configure_hermes(path, name, args.url, dry_run=args.dry_run)


def remove_config(args: argparse.Namespace) -> None:
    name = safe_name(args.name)
    for client in parse_clients(args.clients):
        path = client_path(args, client)
        if client == "codex":
            remove_config_codex(path, name, dry_run=args.dry_run)
        elif client == "zed":
            remove_config_zed(path, name, dry_run=args.dry_run)
        elif client == "hermes":
            remove_config_hermes(path, name, dry_run=args.dry_run, delete=args.delete)


def add_service_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--label")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)


def add_config_common(parser: argparse.ArgumentParser, *, include_name: bool = True) -> None:
    if include_name:
        parser.add_argument("--name", required=True)
    parser.add_argument("--clients", default="codex,zed,hermes")
    parser.add_argument("--codex-config", type=Path)
    parser.add_argument("--zed-config", type=Path)
    parser.add_argument("--hermes-config", type=Path)
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local MCP services and client config.")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install-service", help="create or replace a macOS LaunchAgent")
    add_service_common(install)
    install.add_argument("--playwright-edge", action="store_true", help="run npx @playwright/mcp with Edge extension mode")
    install.add_argument("--shell-command", help="shell command to run under /bin/sh -lc")
    install.add_argument("--command", help="program path to run directly")
    install.add_argument("--arg", action="append", help="argument for --command; repeat as needed")
    install.add_argument("--env", action="append", help="LaunchAgent environment variable as KEY=VALUE; repeat as needed")
    install.add_argument("--stdout")
    install.add_argument("--stderr")
    install.add_argument("--force", action="store_true")
    install.add_argument("--no-keepalive", action="store_true")
    install.set_defaults(func=install_service)

    uninstall = sub.add_parser("uninstall-service", help="unload and remove a managed macOS LaunchAgent")
    add_service_common(uninstall)
    uninstall.add_argument("--keep-plist", action="store_true")
    uninstall.add_argument("--kill-port", action="store_true", help="also kill listeners on --port; use only with explicit user approval")
    uninstall.set_defaults(func=uninstall_service)

    status = sub.add_parser("status", help="show LaunchAgent, port, and optional MCP probe status")
    add_service_common(status)
    status.add_argument("--url")
    status.set_defaults(func=status_service)

    doc = sub.add_parser("doctor", help="status plus client config checks")
    add_service_common(doc)
    doc.add_argument("--url")
    add_config_common(doc, include_name=False)
    doc.set_defaults(func=doctor)

    cfg = sub.add_parser("configure", help="add or update Codex/Zed/Hermes URL config")
    add_config_common(cfg)
    cfg.add_argument("--url", required=True)
    cfg.set_defaults(func=configure)

    rm_cfg = sub.add_parser("remove-config", help="remove or disable Codex/Zed/Hermes config")
    add_config_common(rm_cfg)
    rm_cfg.add_argument("--delete", action="store_true", help="delete Hermes server block instead of setting enabled: false")
    rm_cfg.set_defaults(func=remove_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

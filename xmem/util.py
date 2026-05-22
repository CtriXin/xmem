from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def home_dir() -> Path:
    explicit = os.environ.get("XMEM_HOME")
    if explicit:
        return Path(explicit).expanduser()
    host_home = os.environ.get("XMEM_HOST_HOME") or os.environ.get("MMS_HOST_HOME") or os.environ.get("HOST_HOME")
    if host_home:
        base = Path(host_home).expanduser()
        return base if base.name == ".xmem" else base / ".xmem"
    current_home = Path.home()
    real_home = real_user_home()
    if is_isolated_home(current_home, real_home):
        return real_home / ".xmem"
    return current_home / ".xmem"


def real_user_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).expanduser()
    except Exception:
        return Path.home()


def is_isolated_home(current_home: Path, real_home: Path) -> bool:
    current = str(current_home)
    real = str(real_home)
    if current == real:
        return False
    markers = ("/.config/mms/", "/.codex/", "/.claude/", "/.opencode/", "/.agents/")
    return any(marker in current for marker in markers) and real_home.exists()


def run(cmd: List[str], cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def git_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    out = run(["git", "rev-parse", "--show-toplevel"], cwd=path if path.is_dir() else path.parent)
    return Path(out).resolve() if out else path


def git_value(root: Path, *args: str) -> str:
    return run(["git", *args], cwd=root)


def slugify(value: str, fallback: str = "project") -> str:
    value = value.strip().lower()
    value = value.replace("://", "-").replace("/", "-").replace(":", "-")
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or fallback


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def load_jsonl(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:] if limit else rows


def emit_yaml(data: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(data, dict):
        lines: List[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(emit_yaml(value, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(value)}")
        return "\n".join(lines)
    if isinstance(data, list):
        if not data:
            return f"{pad}[]"
        lines = []
        for value in data:
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(emit_yaml(value, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(value)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(data)}"


def yaml_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or text.strip() != text or any(ch in text for ch in [":", "#", "[", "]", "{", "}", "\n", "\""]):
        return json.dumps(text, ensure_ascii=False)
    return text


def field_from_text(text: str, name: str) -> str:
    patterns = [
        rf"^\s*-\s*{re.escape(name)}\s*:\s*(.*)$",
        rf"^\s*{re.escape(name)}\s*:\s*(.*)$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"')
    return ""


def front_value(text: str, name: str) -> str:
    m = re.search(rf"^\s*{re.escape(name)}\s*:\s*(.*)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"') if m else ""


def list_after_key(text: str, key: str) -> List[str]:
    lines = text.splitlines()
    out: List[str] = []
    capture = False
    base_indent = 0
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*:\s*$", line):
            capture = True
            base_indent = len(line) - len(line.lstrip())
            continue
        if capture:
            indent = len(line) - len(line.lstrip())
            if line.strip() and indent <= base_indent and not line.lstrip().startswith("-"):
                break
            m = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if m:
                out.append(m.group(1).strip().strip('"'))
    return out


def query_terms(query: str) -> List[str]:
    raw = re.findall(r"[A-Za-z0-9_.:-]+|[\u4e00-\u9fff]+", normalize_text(query, loose=False))
    terms: List[str] = []
    for term in raw:
        if term not in terms:
            terms.append(term)
        if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 2:
            for size in (2, 3):
                for i in range(0, len(term) - size + 1):
                    gram = term[i:i + size]
                    if gram not in terms:
                        terms.append(gram)
    return terms or [query.lower().strip()]


_CN_DIGITS = str.maketrans({
    "零": "0", "〇": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5",
    "６": "6", "７": "7", "８": "8", "９": "9",
})


def normalize_text(value: Any, loose: bool = True) -> str:
    """Normalize query/card text for alias matching without changing stored data."""
    text = str(value or "").lower().translate(_CN_DIGITS)
    text = text.replace("模版", "模板")
    if loose:
        # Domain-specific but useful: oral names often omit the middle "小说".
        text = text.replace("小说", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，,。；;：:、/|()（）\[\]【】{}\"'`~!！?？#]+", "", text)
    return text


def query_variants(query: str) -> List[str]:
    variants = [query.lower().strip(), normalize_text(query, loose=False), normalize_text(query, loose=True)]
    out: List[str] = []
    for item in variants:
        if item and item not in out:
            out.append(item)
    return out


def flatten_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from flatten_strings(v)
    elif isinstance(value, list):
        for item in value:
            yield from flatten_strings(item)
    else:
        yield str(value)

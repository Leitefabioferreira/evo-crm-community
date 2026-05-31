#!/usr/bin/env python3
"""Boot-time patches for evo-nexus-dashboard container."""
import json, os, pathlib, shutil, subprocess

# --- 0. Install system dependencies missing from image ---
if not shutil.which('make'):
    subprocess.run(['apt-get', 'install', '-y', '-qq', 'make'], capture_output=True)
    print('[boot] make instalado')
else:
    print('[boot] make ja disponivel')

if not shutil.which('jq'):
    subprocess.run(['apt-get', 'install', '-y', '-qq', 'jq'], capture_output=True)
    print('[boot] jq instalado')
else:
    print('[boot] jq ja disponivel')

# --- 1. Write .claude.json ---
key = os.environ.get('ANTHROPIC_API_KEY', '')
cfg = {
    'theme': 'dark',
    'hasCompletedOnboarding': True,
    'hasSeenWelcome': True,
    'bypassPermissionsModeAccepted': True,
    'telemetry': False,
    'primaryApiKey': key
}
pathlib.Path('/root/.claude').mkdir(parents=True, exist_ok=True)
json.dump(cfg, open('/root/.claude.json', 'w'))
print(f"[boot] .claude.json escrito, primaryApiKey = {key[:20]}..." if key else "[boot] AVISO: ANTHROPIC_API_KEY vazio")

# --- 2. Patch heartbeat_runner.py ---
f = pathlib.Path('/workspace/dashboard/backend/heartbeat_runner.py')
if not f.exists():
    print('[boot] heartbeat_runner.py nao encontrado, pulando patch')
else:
    t = f.read_text()
    changed = 0

    old1 = ('def step1_load_identity(agent: str) -> str:\n'
            '    """Read .claude/agents/{agent}.md and return persona text."""\n'
            '    agent_file = AGENTS_DIR / f"{agent}.md"\n'
            '    if not agent_file.exists():\n'
            '        raise FileNotFoundError(f"Agent file not found: {agent_file}")\n'
            '    return agent_file.read_text(encoding="utf-8")')
    new1 = ('def step1_load_identity(agent: str) -> str:\n'
            '    """Read .claude/agents/{agent}.md and return persona text (strips YAML frontmatter)."""\n'
            '    agent_file = AGENTS_DIR / f"{agent}.md"\n'
            '    if not agent_file.exists():\n'
            '        raise FileNotFoundError(f"Agent file not found: {agent_file}")\n'
            '    text = agent_file.read_text(encoding="utf-8")\n'
            '    if text.startswith("---"):\n'
            '        end = text.find("---", 3)\n'
            '        if end != -1:\n'
            '            text = text[end + 3:].lstrip(chr(10))\n'
            '    return text')

    old2 = '        prompt,  # positional argument — Claude CLI does not have a -p flag\n    ]'
    new2 = '    ]'

    old3 = ('        proc = subprocess.Popen(\n'
            '            cmd,\n'
            '            stdout=subprocess.PIPE,\n'
            '            stderr=subprocess.PIPE,')
    new3 = ('        proc = subprocess.Popen(\n'
            '            cmd,\n'
            '            stdin=subprocess.PIPE,\n'
            '            stdout=subprocess.PIPE,\n'
            '            stderr=subprocess.PIPE,')

    old4 = '            stdout, stderr = proc.communicate(timeout=timeout_seconds)'
    new4 = '            stdout, stderr = proc.communicate(input=prompt, timeout=timeout_seconds)'

    # Patch 5: add --bare, remove --dangerously-skip-permissions, add specific --allowedTools
    # Bash(*) wildcard triggers bypassPermissions mode which fails as root.
    # Instead we list specific Bash patterns that cover Levi's workflow.
    # settings.json permissions.allow is NOT honoured by openclaude+OpenAI backend.
    old5 = '        "--print",\n        "--max-turns", str(max_turns),\n        "--dangerously-skip-permissions",\n        "--output-format", "json",\n'
    new5 = (
        '        "--print",\n'
        '        "--bare",\n'
        '        "--max-turns", str(max_turns),\n'
        '        "--output-format", "json",\n'
        '        "--allowedTools", "Bash(python3 *)",\n'
        '        "--allowedTools", "Bash(curl *)",\n'
        '        "--allowedTools", "Bash(cat *)",\n'
        '        "--allowedTools", "Bash(ls *)",\n'
        '        "--allowedTools", "Bash(echo *)",\n'
        '        "--allowedTools", "Bash(mkdir *)",\n'
        '        "--allowedTools", "Bash(cp *)",\n'
        '        "--allowedTools", "Bash(mv *)",\n'
        '        "--allowedTools", "Bash(grep *)",\n'
        '        "--allowedTools", "Bash(jq *)",\n'
        '        "--allowedTools", "Bash(sed *)",\n'
        '        "--allowedTools", "Bash(awk *)",\n'
        '        "--allowedTools", "Bash(find *)",\n'
        '        "--allowedTools", "Bash(git *)",\n'
        '        "--allowedTools", "Bash(gog *)",\n'
        '        "--allowedTools", "Read(*)",\n'
        '        "--allowedTools", "Write(*)",\n'
        '        "--allowedTools", "Edit(*)",\n'
    )

    # Patch 6: multi-provider fallback + IS_SANDBOX bypass
    # Chain: OpenAI direct → OpenRouter paid → OpenRouter free
    old6 = ('        proc = subprocess.Popen(\n'
            '            cmd,\n'
            '            stdin=subprocess.PIPE,\n'
            '            stdout=subprocess.PIPE,\n'
            '            stderr=subprocess.PIPE,\n'
            '            text=True,\n'
            '            cwd=str(WORKSPACE),\n'
            '            start_new_session=True,  # new process group for clean kill\n'
            '        )')
    new6 = ('        import urllib.request as _urllib_req, urllib.error as _urllib_err, json as _json_p, logging as _log_p\n'
            '        _log_hb = _log_p.getLogger("heartbeat_runner")\n'
            '\n'
            '        def _check_openai_compat(base_url, api_key, model):\n'
            '            """Pre-flight para endpoints OpenAI-compatíveis (apenas OpenRouter free)."""\n'
            '            try:\n'
            '                body = _json_p.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}).encode()\n'
            '                req = _urllib_req.Request(f"{base_url}/chat/completions", data=body,\n'
            '                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})\n'
            '                with _urllib_req.urlopen(req, timeout=8) as r:\n'
            '                    return r.status < 500\n'
            '            except _urllib_err.HTTPError as ex:\n'
            '                return ex.code not in (401, 402, 403, 429)\n'
            '            except Exception:\n'
            '                return False\n'
            '\n'
            '        # POLÍTICA: apenas modelos FREE do OpenRouter são permitidos.\n'
            '        # Serviços pagos (OpenAI direct, Anthropic direct, OpenRouter pago) NÃO autorizados.\n'
            '        _openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")\n'
            '\n'
            '        def _env_openai_compat(base_url, api_key, model):\n'
            '            e = dict(os.environ)\n'
            '            e["IS_SANDBOX"] = "1"\n'
            '            e["CLAUDE_CODE_USE_OPENAI"] = "1"\n'
            '            e["OPENAI_BASE_URL"] = base_url\n'
            '            e["OPENAI_API_KEY"] = api_key\n'
            '            e["OPENAI_MODEL"] = model\n'
            '            e["CLAUDE_CODE_PROVIDER_PROFILE_ENV_APPLIED"] = "1"\n'
            '            return e\n'
            '\n'
            '        _provider_chain = [\n'
            '            # 1: OpenRouter free — Gemma 4 26B (confirmado HTTP 200)\n'
            '            ("OpenRouter-free/gemma-4-26b",\n'
            '             lambda: bool(_openrouter_key) and _check_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "google/gemma-4-26b-a4b-it:free"),\n'
            '             lambda: _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "google/gemma-4-26b-a4b-it:free"),\n'
            '             lambda c: c),\n'
            '            # 2: OpenRouter free — Nemotron 120B (fallback, confirmado HTTP 200)\n'
            '            ("OpenRouter-free/nemotron-120b",\n'
            '             lambda: bool(_openrouter_key),\n'
            '             lambda: _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "nvidia/nemotron-3-super-120b-a12b:free"),\n'
            '             lambda c: c),\n'
            '        ]\n'
            '\n'
            '        _selected_env = None\n'
            '        _selected_cmd = cmd\n'
            '        for _label, _check, _mk_env, _mk_cmd in _provider_chain:\n'
            '            try:\n'
            '                if _check():\n'
            '                    _selected_env = _mk_env()\n'
            '                    _selected_cmd = _mk_cmd(list(cmd))\n'
            '                    _log_hb.info(f"[provider] {_label} selecionado")\n'
            '                    print(f"[provider] {_label}", flush=True)\n'
            '                    break\n'
            '            except Exception as _e:\n'
            '                _log_hb.warning(f"[provider] {_label} check falhou: {_e}")\n'
            '        if _selected_env is None:\n'
            '            _selected_env = _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "nvidia/nemotron-3-super-120b-a12b:free")\n'
            '            print("[provider] fallback final: nemotron free", flush=True)\n'
            '        proc = subprocess.Popen(\n'
            '            _selected_cmd,\n'
            '            stdin=subprocess.PIPE,\n'
            '            stdout=subprocess.PIPE,\n'
            '            stderr=subprocess.PIPE,\n'
            '            text=True,\n'
            '            env=_selected_env,\n'
            '            cwd=str(WORKSPACE),\n'
            '            start_new_session=True,  # new process group for clean kill\n'
            '        )')

    # Patch 7: use openclaude (OpenAI-compatible wrapper) instead of claude when
    # CLAUDE_CODE_USE_OPENAI=1 — the claude binary ignores OpenAI env vars because
    # it has stored Anthropic credentials that take priority.
    old7 = ('    claude_bin = shutil.which("claude")\n'
            '    if not claude_bin:\n'
            '        return {\n'
            '            "status": "fail",\n'
            '            "error": "claude binary not found in PATH",')
    new7 = ('    _use_openai = os.environ.get("CLAUDE_CODE_USE_OPENAI") == "1"\n'
            '    claude_bin = shutil.which("openclaude") if _use_openai else shutil.which("claude")\n'
            '    if not claude_bin:\n'
            '        claude_bin = shutil.which("claude") or shutil.which("openclaude")\n'
            '    if not claude_bin:\n'
            '        return {\n'
            '            "status": "fail",\n'
            '            "error": "claude/openclaude binary not found in PATH",')

    # Patch 8: keep ANTHROPIC_API_KEY for openclaude auth validation; actual LLM calls use OPENAI_BASE_URL.
    # Remove only OPENAI_API_KEY_DIRECT (a legacy env that could override the provider chain).
    old8 = ('            e["CLAUDE_CODE_PROVIDER_PROFILE_ENV_APPLIED"] = "1"\n'
            '            return e\n')
    new8 = ('            e["CLAUDE_CODE_PROVIDER_PROFILE_ENV_APPLIED"] = "1"\n'
            '            # Keep ANTHROPIC_API_KEY for openclaude auth validation; actual calls go via OPENAI_BASE_URL\n'
            '            e.pop("OPENAI_API_KEY_DIRECT", None)\n'
            '            return e\n')

    # Patch 9: Add Groq provider to chain (priority 0, free, high limits)
    # Only applies if the chain was already patched with Patch 6 (new6 style).
    old9 = (
        '        _openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")\n'
        '\n'
        '        def _env_openai_compat(base_url, api_key, model):\n'
    )
    new9 = (
        '        _openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")\n'
        '        _groq_key = os.environ.get("GROQ_API_KEY", "")\n'
        '\n'
        '        def _env_openai_compat(base_url, api_key, model):\n'
    )

    old9b = (
        '        _provider_chain = [\n'
        '            # 1: OpenRouter free — Gemma 4 26B (confirmado HTTP 200)\n'
        '            ("OpenRouter-free/gemma-4-26b",\n'
        '             lambda: bool(_openrouter_key) and _check_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "google/gemma-4-26b-a4b-it:free"),\n'
        '             lambda: _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "google/gemma-4-26b-a4b-it:free"),\n'
        '             lambda c: c),\n'
        '            # 2: OpenRouter free — Nemotron 120B (fallback, confirmado HTTP 200)\n'
        '            ("OpenRouter-free/nemotron-120b",\n'
        '             lambda: bool(_openrouter_key),\n'
        '             lambda: _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "nvidia/nemotron-3-super-120b-a12b:free"),\n'
        '             lambda c: c),\n'
        '        ]\n'
    )
    new9b = (
        '        _provider_chain = [\n'
        '            # 0: Groq (gratuito, alta velocidade) — prioridade maxima quando GROQ_API_KEY definida\n'
        '            ("Groq/llama-3.3-70b",\n'
        '             lambda: bool(_groq_key) and _check_openai_compat("https://api.groq.com/openai/v1", _groq_key, "llama-3.3-70b-versatile"),\n'
        '             lambda: _env_openai_compat("https://api.groq.com/openai/v1", _groq_key, "llama-3.3-70b-versatile"),\n'
        '             lambda c: c),\n'
        '            # 0b: Groq fallback — llama-3.1-8b-instant\n'
        '            ("Groq/llama-3.1-8b",\n'
        '             lambda: bool(_groq_key),\n'
        '             lambda: _env_openai_compat("https://api.groq.com/openai/v1", _groq_key, "llama-3.1-8b-instant"),\n'
        '             lambda c: c),\n'
        '            # 1: OpenRouter free — Gemma 4 26B (confirmado HTTP 200)\n'
        '            ("OpenRouter-free/gemma-4-26b",\n'
        '             lambda: bool(_openrouter_key) and _check_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "google/gemma-4-26b-a4b-it:free"),\n'
        '             lambda: _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "google/gemma-4-26b-a4b-it:free"),\n'
        '             lambda c: c),\n'
        '            # 2: OpenRouter free — Nemotron 120B (fallback)\n'
        '            ("OpenRouter-free/nemotron-120b",\n'
        '             lambda: bool(_openrouter_key) and _check_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "nvidia/nemotron-3-super-120b-a12b:free"),\n'
        '             lambda: _env_openai_compat("https://openrouter.ai/api/v1", _openrouter_key, "nvidia/nemotron-3-super-120b-a12b:free"),\n'
        '             lambda c: c),\n'
        '        ]\n'
    )

    for old, new in [(old1, new1), (old2, new2), (old3, new3), (old4, new4), (old5, new5), (old6, new6), (old7, new7), (old8, new8), (old9, new9), (old9b, new9b)]:
        if old in t:
            t = t.replace(old, new)
            changed += 1

    # Safety cleanup: always remove --dangerously-skip-permissions (fails as root)
    # and --allowedTools wildcard (Bash(*) triggers bypassPermissions mode which also fails as root)
    if '"--dangerously-skip-permissions"' in t:
        t = t.replace('        "--dangerously-skip-permissions",\n', '')
        print('[boot] heartbeat_runner.py: --dangerously-skip-permissions removido (cleanup)')
    if '"--allowedTools", "Bash(*) Read(*) Write(*) Edit(*) Skill(*) Agent(*)"' in t:
        t = t.replace('        "--allowedTools", "Bash(*) Read(*) Write(*) Edit(*) Skill(*) Agent(*)",\n', '')
        print('[boot] heartbeat_runner.py: --allowedTools wildcard removido (cleanup)')

    # Delete stale pyc cache
    cache_dir = pathlib.Path('/workspace/dashboard/backend/__pycache__')

# --- 3. Patch claude-bridge.js: allow any model in terminal (remove chat-only block) ---
cb = pathlib.Path('/workspace/dashboard/terminal-server/src/claude-bridge.js')
if cb.exists():
    cb_text = cb.read_text()
    old_cb = "if (providerConfig.active !== 'anthropic' && providerMode !== 'code') {"
    new_cb = "if (false && providerConfig.active !== 'anthropic' && providerMode !== 'code') {"
    if old_cb in cb_text:
        cb.write_text(cb_text.replace(old_cb, new_cb))
        print('[boot] claude-bridge.js patched: removed chat-model terminal restriction')
    elif new_cb in cb_text:
        print('[boot] claude-bridge.js already patched (chat restriction disabled)')
    else:
        print('[boot] claude-bridge.js: restriction line not found, skipping')
    if cache_dir.exists():
        for pyc in cache_dir.glob('heartbeat_runner*.pyc'):
            pyc.unlink(missing_ok=True)

    f.write_text(t)
    print(f'[boot] heartbeat_runner.py patched ({changed}/10 fixes applied)')

# --- 4. Ensure evo_utils package exists with all required methods ---
evo_utils_dir = pathlib.Path('/workspace/evo_utils')
evo_utils_dir.mkdir(exist_ok=True)

_init = evo_utils_dir / '__init__.py'
if not _init.exists():
    _init.write_text('"""evo_utils — utilitários internos do EvoNexus."""\n')

_logger = evo_utils_dir / 'logger.py'
if not _logger.exists():
    _logger.write_text('''"""Logger padronizado para scripts EvoNexus."""
import logging, sys

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(h)
    logger.setLevel(level)
    return logger
''')

_html = evo_utils_dir / 'html_report.py'
_html.write_text('''"""HTMLReport — gerador de relatórios HTML para rotinas EvoNexus."""
import datetime
from pathlib import Path

class HTMLReport:
    def __init__(self, title: str, report_id: str, output_dir: str = \'/workspace/ADWs/logs\'):
        self.title = title
        self.report_id = report_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._sections: list = []
        self._data: dict = {}

    def add_title(self, text: str) -> None:
        self._sections.append(f\'<h1>{text}</h1>\')

    def add_heading(self, text: str, level: int = 2) -> None:
        self._sections.append(f\'<h{level}>{text}</h{level}>\')

    def add_paragraph(self, text: str) -> None:
        self._sections.append(f\'<p>{text}</p>\')

    # aliases used by various routines
    def add_text(self, text: str) -> None:
        self.add_paragraph(text)

    def add_error(self, text: str) -> None:
        self.add_alert(text, level=\'error\')

    def add_warning(self, text: str) -> None:
        self.add_alert(text, level=\'warning\')

    def add_success(self, text: str) -> None:
        self.add_alert(text, level=\'success\')

    def add_metric(self, label: str, value, unit: str = \'\') -> None:
        self._sections.append(f\'<div class="metric"><span class="label">{label}</span><span class="value">{value}{unit}</span></div>\')
        self._data[label] = value

    def add_table(self, headers: list, rows: list) -> None:
        th = \'\'.join(f\'<th>{h}</th>\' for h in headers)
        trs = \'\'.join(f\'<tr>{\'\'.join(f\'<td>{c}</td>\' for c in row)}</tr>\' for row in rows)
        self._sections.append(f\'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>\')

    def add_list(self, items: list, ordered: bool = False) -> None:
        tag = \'ol\' if ordered else \'ul\'
        lis = \'\'.join(f\'<li>{i}</li>\' for i in items)
        self._sections.append(f\'<{tag}>{lis}</{tag}>\')

    def add_alert(self, text: str, level: str = \'info\') -> None:
        self._sections.append(f\'<div class="alert alert-{level}">{text}</div>\')

    def get_data(self) -> dict:
        return self._data

    def save(self, filename=None) -> Path:
        ts = datetime.datetime.now().strftime(\'%Y%m%d_%H%M%S\')
        fname = filename or f\'{self.report_id}_{ts}.html\'
        path = self.output_dir / fname
        body = \'\\n\'.join(self._sections)
        now = datetime.datetime.now().strftime(\'%d/%m/%Y %H:%M:%S\')
        html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>{self.title}</title>
<style>body{{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#333}}
h1{{color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:10px}}h2,h3{{color:#34495e}}
table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #ddd;padding:8px 12px;text-align:left}}
th{{background:#3498db;color:white}}tr:nth-child(even){{background:#f2f2f2}}
.metric{{display:flex;justify-content:space-between;padding:8px;border-bottom:1px solid #eee}}
.value{{font-weight:bold;color:#2980b9}}.alert{{padding:12px;margin:8px 0;border-radius:4px}}
.alert-info{{background:#d1ecf1;border:1px solid #bee5eb}}.alert-warning{{background:#fff3cd;border:1px solid #ffc107}}
.alert-error{{background:#f8d7da;border:1px solid #f5c6cb}}.alert-success{{background:#d4edda;border:1px solid #c3e6cb}}</style>
</head><body><p><em>Gerado em: {now}</em></p>{body}</body></html>"""
        path.write_text(html, encoding=\'utf-8\')
        return path

    def print_summary(self) -> None:
        print(f\'[{self.report_id}] {self.title}\')
        for k, v in self._data.items():
            print(f\'  {k}: {v}\')
''')
print('[boot] evo_utils verificado/atualizado')

# --- 5. Create /usr/local/bin/evo-run wrapper (persists across restarts via boot) ---
_evo_run = pathlib.Path('/usr/local/bin/evo-run')
_evo_run.write_text(r'''#!/bin/bash
# evo-run — executa rotinas ADW pelo Oracle terminal
# Uso:  evo-run <rotina>   ou   R=<rotina> evo-run

ROUTINE="${1:-${R}}"
WORKSPACE="/workspace"
cd "$WORKSPACE" || exit 1

# Garante que /workspace esta no PYTHONPATH para evo_utils e outros pacotes locais
export PYTHONPATH="/workspace:${PYTHONPATH}"

case "$ROUTINE" in
  fin-pulse|financial-pulse)
    uv run python ADWs/routines/custom/financial_pulse.py ;;
  morning|good-morning)
    uv run python ADWs/routines/good_morning.py ;;
  eod|end-of-day)
    uv run python ADWs/routines/end_of_day.py ;;
  memory|memory-sync)
    uv run python ADWs/routines/memory_sync.py ;;
  weekly|weekly-review)
    uv run python ADWs/routines/weekly_review.py ;;
  backup|data-backup)
    uv run python ADWs/routines/backup.py ;;
  brain-health)
    uv run python ADWs/routines/brain_health.py ;;
  social-analytics|social|social_analytics)
    uv run python ADWs/routines/custom/social_analytics.py ;;
  *)
    echo "Rotina desconhecida: $ROUTINE"
    echo "Disponiveis: fin-pulse, morning, eod, memory, weekly, backup, brain-health, social-analytics"
    exit 1 ;;
esac
''')
import os as _os
_os.chmod('/usr/local/bin/evo-run', 0o755)
print('[boot] /usr/local/bin/evo-run criado/atualizado')

# --- 6. Ensure social_analytics.py exists in ADWs/routines/custom/ ---
_social_dir = pathlib.Path('/workspace/ADWs/routines/custom')
_social_dir.mkdir(parents=True, exist_ok=True)
_social_py = _social_dir / 'social_analytics.py'

# If original exists in dashboard/backend/routines/, use it as base
_orig_social = pathlib.Path('/workspace/dashboard/backend/routines/social_analytics.py')
if _orig_social.exists() and not _social_py.exists():
    import shutil as _shutil2
    _shutil2.copy2(str(_orig_social), str(_social_py))
    print('[boot] social_analytics.py copiado de dashboard/backend/routines/')
elif not _social_py.exists():
    _social_py.write_text(r'''"""
social_analytics.py — Análise de métricas sociais e WhatsApp (EvoNexus ADW)
"""
import os, sys, json, datetime, requests
from pathlib import Path

# Garante que /workspace está no path para evo_utils
sys.path.insert(0, '/workspace')

try:
    from evo_utils.html_report import HTMLReport
    from evo_utils.logger import get_logger
except ImportError:
    # Fallback inline mínimo
    class HTMLReport:
        def __init__(self, title, report_id, output_dir='/workspace/ADWs/logs'):
            self.title = title; self.report_id = report_id
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            self._sections = []; self._data = {}
            self.output_dir = Path(output_dir)
        def add_heading(self, t, level=2): self._sections.append(f'<h{level}>{t}</h{level}>')
        def add_text(self, t): self._sections.append(f'<p>{t}</p>')
        def add_metric(self, l, v, u=''): self._data[l]=v; self._sections.append(f'<p><b>{l}:</b> {v}{u}</p>')
        def add_success(self, t): self._sections.append(f'<p style="color:green">{t}</p>')
        def add_error(self, t): self._sections.append(f'<p style="color:red">{t}</p>')
        def add_warning(self, t): self._sections.append(f'<p style="color:orange">{t}</p>')
        def save(self):
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            p = self.output_dir / f'{self.report_id}_{ts}.html'
            p.write_text('\n'.join(self._sections), encoding='utf-8'); return p
        def print_summary(self):
            print(f'[{self.report_id}] {self.title}')
            for k,v in self._data.items(): print(f'  {k}: {v}')
    import logging
    def get_logger(name, level=logging.INFO):
        logging.basicConfig(level=level, format='[%(asctime)s] %(levelname)s: %(message)s')
        return logging.getLogger(name)

logger = get_logger('social_analytics')
report = HTMLReport('Social Analytics — EvoNexus', 'social_analytics')

EVOLUTION_URL = os.environ.get('EVOLUTION_API_URL', 'http://evolution-api:8080')
EVOLUTION_KEY  = os.environ.get('EVOLUTION_API_KEY', '')
CRM_URL        = os.environ.get('EVO_CRM_URL', 'http://evo-crm:3000')
CRM_TOKEN      = os.environ.get('EVO_CRM_API_TOKEN', '')

report.add_heading('Social Analytics', level=1)
report.add_text(f'Gerado em: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')

errors = []

# --- Evolution API: lista instâncias e mensagens recentes ---
try:
    headers = {'apikey': EVOLUTION_KEY, 'Content-Type': 'application/json'}
    r = requests.get(f'{EVOLUTION_URL}/instance/fetchInstances', headers=headers, timeout=10)
    if r.ok:
        instances = r.json()
        if isinstance(instances, list):
            report.add_heading('WhatsApp — Instâncias', level=2)
            report.add_metric('Total de instâncias', len(instances))
            connected = sum(1 for i in instances if isinstance(i, dict) and i.get('connectionStatus') == 'open')
            report.add_metric('Instâncias conectadas', connected)
            for inst in instances[:5]:
                name = inst.get('name', '?') if isinstance(inst, dict) else str(inst)
                status = inst.get('connectionStatus', '?') if isinstance(inst, dict) else '?'
                report.add_text(f'• {name}: {status}')
            logger.info(f'Evolution API: {len(instances)} instâncias, {connected} conectadas')
        else:
            report.add_warning('Evolution API retornou formato inesperado')
    else:
        report.add_warning(f'Evolution API: {r.status_code} — {r.text[:200]}')
        errors.append(f'Evolution API HTTP {r.status_code}')
except Exception as e:
    report.add_warning(f'Evolution API indisponível: {e}')
    errors.append(str(e))
    logger.warning(f'Evolution API error: {e}')

# --- CRM: leads e contatos recentes ---
try:
    crm_headers = {'Authorization': f'Bearer {CRM_TOKEN}', 'Content-Type': 'application/json'}
    r2 = requests.get(f'{CRM_URL}/contacts?limit=1', headers=crm_headers, timeout=10)
    if r2.ok:
        data = r2.json()
        total = data.get('total', data.get('count', '?')) if isinstance(data, dict) else '?'
        report.add_heading('CRM — Contatos', level=2)
        report.add_metric('Total de contatos', total)
        logger.info(f'CRM: {total} contatos')
    else:
        report.add_warning(f'CRM API: {r2.status_code}')
        errors.append(f'CRM HTTP {r2.status_code}')
except Exception as e:
    report.add_warning(f'CRM indisponível: {e}')
    errors.append(str(e))
    logger.warning(f'CRM error: {e}')

# --- Resultado final ---
report.add_heading('Resumo', level=2)
if errors:
    report.add_warning(f'Avisos: {len(errors)} serviço(s) com problema')
    for e in errors:
        report.add_text(f'  ⚠ {e}')
else:
    report.add_success('Todos os serviços responderam normalmente')

path = report.save()
report.print_summary()
logger.info(f'Relatório salvo: {path}')
print(f'[social_analytics] Concluído. Relatório: {path}')
''')
    print('[boot] social_analytics.py criado em ADWs/routines/custom/')
else:
    print('[boot] social_analytics.py ja existe, mantendo')

# --- 6b. Ensure financial_pulse.py exists in ADWs/routines/custom/ ---
_fp_py = _social_dir / 'financial_pulse.py'
if not _fp_py.exists():
    _fp_py.write_text(r'''"""financial_pulse.py — resumo financeiro diário (EvoNexus ADW)"""
import os, sys, datetime
sys.path.insert(0, '/workspace')
try:
    from evo_utils.html_report import HTMLReport
    from evo_utils.logger import get_logger
except ImportError:
    from pathlib import Path as _P
    class HTMLReport:
        def __init__(self, t, r, output_dir='/workspace/ADWs/logs'):
            _P(output_dir).mkdir(parents=True, exist_ok=True)
            self._s=[]; self._d={}; self.output_dir=_P(output_dir); self.report_id=r; self.title=t
        def add_heading(self,t,level=2): self._s.append(f'<h{level}>{t}</h{level}>')
        def add_text(self,t): self._s.append(f'<p>{t}</p>')
        def add_metric(self,l,v,u=''): self._d[l]=v; self._s.append(f'<p><b>{l}:</b> {v}{u}</p>')
        def add_success(self,t): self._s.append(f'<p style="color:green">{t}</p>')
        def add_error(self,t): self._s.append(f'<p style="color:red">{t}</p>')
        def add_warning(self,t): self._s.append(f'<p style="color:orange">{t}</p>')
        def save(self):
            ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            p=self.output_dir/f'{self.report_id}_{ts}.html'; p.write_text('\n'.join(self._s),encoding='utf-8'); return p
        def print_summary(self):
            print(f'[{self.report_id}] {self.title}')
            for k,v in self._d.items(): print(f'  {k}: {v}')
    import logging
    def get_logger(n, level=logging.INFO): logging.basicConfig(level=level); return logging.getLogger(n)

logger = get_logger('financial_pulse')
report = HTMLReport('Financial Pulse — EvoNexus', 'financial_pulse')
report.add_heading('Financial Pulse', level=1)
report.add_text(f'Gerado em: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')

STRIPE_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
OMIE_KEY   = os.environ.get('OMIE_APP_KEY', '')
ASAAS_KEY  = os.environ.get('ASAAS_API_KEY', '')

if not any([STRIPE_KEY, OMIE_KEY, ASAAS_KEY]):
    report.add_warning('Nenhuma integração financeira configurada (Stripe, Omie, Asaas).')
    report.add_text('Preencha as chaves em .env.nexus para ativar o Financial Pulse completo.')
    report.add_text('Stripe: STRIPE_SECRET_KEY | Omie: OMIE_APP_KEY | Asaas: ASAAS_API_KEY')
else:
    import requests
    if STRIPE_KEY:
        try:
            r = requests.get('https://api.stripe.com/v1/balance', auth=(STRIPE_KEY,''), timeout=10)
            if r.ok:
                avail = r.json().get('available', [{}])
                amt = avail[0].get('amount',0)/100 if avail else 0
                cur = avail[0].get('currency','brl').upper() if avail else 'BRL'
                report.add_heading('Stripe', level=2)
                report.add_metric('Saldo disponível', f'{amt:.2f}', f' {cur}')
                report.add_success('Stripe conectado')
            else:
                report.add_warning(f'Stripe HTTP {r.status_code}')
        except Exception as e:
            report.add_warning(f'Stripe indisponível: {e}')
    if ASAAS_KEY:
        try:
            base = os.environ.get('ASAAS_BASE_URL', 'https://api.asaas.com')
            r = requests.get(f'{base}/v3/finance/balance',
                             headers={'access_token': ASAAS_KEY}, timeout=10)
            if r.ok:
                report.add_heading('Asaas', level=2)
                report.add_metric('Saldo Asaas', f'R$ {r.json().get("balance",0):.2f}')
                report.add_success('Asaas conectado')
            else:
                report.add_warning(f'Asaas HTTP {r.status_code}')
        except Exception as e:
            report.add_warning(f'Asaas indisponível: {e}')

path = report.save()
report.print_summary()
print(f'[financial_pulse] Concluído. Relatório: {path}')
''')
    print('[boot] financial_pulse.py criado em ADWs/routines/custom/')
else:
    print('[boot] financial_pulse.py ja existe, mantendo')

# --- 7. Fix integrations-health: register in _SYSTEM_HEARTBEAT_SCRIPTS + create watcher ---
_runner = pathlib.Path('/workspace/dashboard/backend/heartbeat_runner.py')
if _runner.exists():
    _rt = _runner.read_text()
    _old_reg = '_SYSTEM_HEARTBEAT_SCRIPTS: dict[str, str] = {\n    "summary-watcher": "summary_watcher",\n}'
    _new_reg = '_SYSTEM_HEARTBEAT_SCRIPTS: dict[str, str] = {\n    "summary-watcher": "summary_watcher",\n    "integrations-health": "integrations_health_watcher",\n}'
    if _old_reg in _rt and _new_reg not in _rt:
        _runner.write_text(_rt.replace(_old_reg, _new_reg))
        print('[boot] heartbeat_runner.py patched: integrations-health registered')
    else:
        print('[boot] heartbeat_runner.py: integrations-health already registered or pattern changed')

_watcher = pathlib.Path('/workspace/dashboard/backend/integrations_health_watcher.py')
_watcher.write_text(r'''"""integrations_health_watcher — verifica saúde das integrações configuradas."""
import os, time, logging
from pathlib import Path

log = logging.getLogger(__name__)

SERVICES = [
    ("evolution-api",  os.environ.get("EVOLUTION_API_URL", "http://evolution-api:8080") + "/",        {"apikey": os.environ.get("EVOLUTION_API_KEY","")}),
    ("evo-crm",        os.environ.get("EVO_CRM_URL",     "http://evo-crm:3000") + "/health/live",     {}),
    ("evo-core",       "http://evo-core:5555/health",                                                   {}),
    ("evo-processor",  "http://evo-processor:8000/health",                                              {}),
    ("evo-bot-runtime","http://evo-bot-runtime:8080/health",                                            {}),
    ("nexus-dashboard","http://localhost:8080/health",                                                   {}),
]

def run_watcher() -> dict:
    try:
        import requests
    except ImportError:
        return {"status": "fail", "error": "requests not installed", "services": {}}

    results = {}
    ok = 0
    fail = 0
    for name, url, headers in SERVICES:
        t0 = time.time()
        try:
            r = requests.get(url, headers=headers, timeout=5)
            ms = int((time.time() - t0) * 1000)
            if r.status_code < 400:
                results[name] = {"status": "ok", "code": r.status_code, "ms": ms}
                ok += 1
            else:
                results[name] = {"status": "warn", "code": r.status_code, "ms": ms}
                fail += 1
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            results[name] = {"status": "fail", "error": str(e)[:80], "ms": ms}
            fail += 1
            log.warning("integrations_health: %s unreachable: %s", name, e)

    # External integrations — only check if key is set
    ext_checks = [
        ("stripe",   bool(os.environ.get("STRIPE_SECRET_KEY"))),
        ("github",   bool(os.environ.get("GITHUB_TOKEN"))),
        ("telegram", bool(os.environ.get("TELEGRAM_BOT_TOKEN"))),
        ("linear",   bool(os.environ.get("LINEAR_API_KEY"))),
        ("notion",   bool(os.environ.get("NOTION_API_KEY"))),
        ("omie",     bool(os.environ.get("OMIE_APP_KEY"))),
        ("asaas",    bool(os.environ.get("ASAAS_API_KEY"))),
        ("todoist",  bool(os.environ.get("TODOIST_API_TOKEN"))),
        ("discord",  bool(os.environ.get("DISCORD_BOT_TOKEN"))),
    ]
    not_configured = [n for n, v in ext_checks if not v]
    configured_ext = [n for n, v in ext_checks if v]

    summary = {
        "status": "ok" if fail == 0 else "warn",
        "services_ok": ok,
        "services_fail": fail,
        "details": results,
        "external_configured": configured_ext,
        "external_missing": not_configured,
    }
    log.info("integrations_health: %d ok / %d fail | ext configured: %s | missing: %s",
             ok, fail, configured_ext, not_configured)

    # Save log
    import json, datetime
    log_dir = Path("/workspace/ADWs/logs/integrations")
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    (log_dir / f"health_{ts}.json").write_text(json.dumps(summary, indent=2))

    return summary
''')
print('[boot] integrations_health_watcher.py criado/atualizado')

# --- 8. Write scheduler-boot.py to config volume (shared with evo-nexus-scheduler) ---
# The scheduler container mounts nexus_config:/workspace/config.
# It runs this file before starting scheduler.py to ensure custom scripts exist.
_sched_boot = pathlib.Path('/workspace/config/scheduler-boot.py')
_sched_boot.write_text(r'''#!/usr/bin/env python3
"""scheduler-boot.py — cria scripts custom no container do scheduler.
Executado automaticamente pelo evo-nexus-scheduler antes de iniciar scheduler.py.
Gerado por nexus-boot.py no container do dashboard.
"""
import pathlib, datetime

CUSTOM = pathlib.Path('/workspace/ADWs/routines/custom')
CUSTOM.mkdir(parents=True, exist_ok=True)

# --- financial_pulse.py ---
_fp = CUSTOM / 'financial_pulse.py'
if not _fp.exists():
    _fp.write_text(r"""
\"\"\"financial_pulse.py — resumo financeiro diário (EvoNexus ADW)\"\"\"
import os, sys, datetime
sys.path.insert(0, '/workspace')
try:
    from evo_utils.html_report import HTMLReport
    from evo_utils.logger import get_logger
except ImportError:
    class HTMLReport:
        def __init__(self, t, r, output_dir='/workspace/ADWs/logs'):
            from pathlib import Path; Path(output_dir).mkdir(parents=True, exist_ok=True)
            self._s=[]; self._d={}; self.output_dir=Path(output_dir); self.report_id=r; self.title=t
        def add_heading(self,t,level=2): self._s.append(f'<h{level}>{t}</h{level}>')
        def add_text(self,t): self._s.append(f'<p>{t}</p>')
        def add_metric(self,l,v,u=''): self._d[l]=v; self._s.append(f'<p><b>{l}:</b> {v}{u}</p>')
        def add_success(self,t): self._s.append(f'<p style="color:green">{t}</p>')
        def add_error(self,t): self._s.append(f'<p style="color:red">{t}</p>')
        def add_warning(self,t): self._s.append(f'<p style="color:orange">{t}</p>')
        def save(self):
            from pathlib import Path; ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            p=self.output_dir/f'{self.report_id}_{ts}.html'; p.write_text('\n'.join(self._s),encoding='utf-8'); return p
        def print_summary(self):
            print(f'[{self.report_id}] {self.title}')
            for k,v in self._d.items(): print(f'  {k}: {v}')
    import logging
    def get_logger(n,level=20): logging.basicConfig(level=level); return logging.getLogger(n)

logger = get_logger('financial_pulse')
report = HTMLReport('Financial Pulse — EvoNexus', 'financial_pulse')
report.add_heading('Financial Pulse', level=1)
report.add_text(f'Gerado em: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')

STRIPE_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
OMIE_KEY   = os.environ.get('OMIE_APP_KEY', '')
ASAAS_KEY  = os.environ.get('ASAAS_API_KEY', '')

if not any([STRIPE_KEY, OMIE_KEY, ASAAS_KEY]):
    report.add_warning('Nenhuma integração financeira configurada (Stripe, Omie, Asaas)')
    report.add_text('Configure as chaves em .env.nexus para ativar o Financial Pulse completo.')
else:
    import requests
    if STRIPE_KEY:
        try:
            r = requests.get('https://api.stripe.com/v1/balance', auth=(STRIPE_KEY,''), timeout=10)
            if r.ok:
                d = r.json()
                avail = d.get('available',[{}])
                amt = avail[0].get('amount',0)/100 if avail else 0
                cur = avail[0].get('currency','brl').upper() if avail else 'BRL'
                report.add_heading('Stripe', level=2)
                report.add_metric('Saldo disponível', f'{amt:.2f}', f' {cur}')
                report.add_success('Stripe conectado')
            else:
                report.add_warning(f'Stripe HTTP {r.status_code}')
        except Exception as e:
            report.add_warning(f'Stripe indisponível: {e}')
    if ASAAS_KEY:
        try:
            base = os.environ.get('ASAAS_BASE_URL','https://api.asaas.com')
            r = requests.get(f'{base}/v3/finance/balance', headers={'access_token': ASAAS_KEY}, timeout=10)
            if r.ok:
                d = r.json(); bal = d.get('balance',0)
                report.add_heading('Asaas', level=2)
                report.add_metric('Saldo Asaas', f'R$ {bal:.2f}')
                report.add_success('Asaas conectado')
            else:
                report.add_warning(f'Asaas HTTP {r.status_code}')
        except Exception as e:
            report.add_warning(f'Asaas indisponível: {e}')

path = report.save()
report.print_summary()
print(f'[financial_pulse] Concluído. Relatório: {path}')
""")
    print(f'[scheduler-boot] financial_pulse.py criado')

# --- social_analytics.py ---
_sa = CUSTOM / 'social_analytics.py'
if not _sa.exists():
    _sa.write_text(r"""
\"\"\"social_analytics.py — métricas WhatsApp/CRM (EvoNexus ADW)\"\"\"
import os, sys, datetime, requests
sys.path.insert(0, '/workspace')
try:
    from evo_utils.html_report import HTMLReport
    from evo_utils.logger import get_logger
except ImportError:
    class HTMLReport:
        def __init__(self, t, r, output_dir='/workspace/ADWs/logs'):
            from pathlib import Path; Path(output_dir).mkdir(parents=True, exist_ok=True)
            self._s=[]; self._d={}; self.output_dir=Path(output_dir); self.report_id=r; self.title=t
        def add_heading(self,t,level=2): self._s.append(f'<h{level}>{t}</h{level}>')
        def add_text(self,t): self._s.append(f'<p>{t}</p>')
        def add_metric(self,l,v,u=''): self._d[l]=v; self._s.append(f'<p><b>{l}:</b> {v}{u}</p>')
        def add_success(self,t): self._s.append(f'<p style="color:green">{t}</p>')
        def add_warning(self,t): self._s.append(f'<p style="color:orange">{t}</p>')
        def save(self):
            from pathlib import Path; ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            p=self.output_dir/f'{self.report_id}_{ts}.html'; p.write_text('\n'.join(self._s),encoding='utf-8'); return p
        def print_summary(self):
            print(f'[{self.report_id}] {self.title}')
            for k,v in self._d.items(): print(f'  {k}: {v}')
    import logging
    def get_logger(n,level=20): logging.basicConfig(level=level); return logging.getLogger(n)

logger = get_logger('social_analytics')
report = HTMLReport('Social Analytics — EvoNexus', 'social_analytics')
EVO_URL = os.environ.get('EVOLUTION_API_URL','http://evolution-api:8080')
EVO_KEY = os.environ.get('EVOLUTION_API_KEY','')
CRM_URL = os.environ.get('EVO_CRM_URL','http://evo-crm:3000')
CRM_TOK = os.environ.get('EVO_CRM_API_TOKEN','')
report.add_heading('Social Analytics', level=1)
report.add_text(f'Gerado em: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')
try:
    r = requests.get(f'{EVO_URL}/instance/fetchInstances', headers={'apikey': EVO_KEY}, timeout=10)
    if r.ok:
        insts = r.json()
        if isinstance(insts, list):
            conn = sum(1 for i in insts if isinstance(i,dict) and i.get('connectionStatus')=='open')
            report.add_metric('Instâncias WhatsApp', len(insts)); report.add_metric('Conectadas', conn)
except Exception as e:
    report.add_warning(f'Evolution API: {e}')
try:
    r2 = requests.get(f'{CRM_URL}/contacts?limit=1', headers={'Authorization': f'Bearer {CRM_TOK}'}, timeout=10)
    if r2.ok:
        d = r2.json(); total = d.get('total', d.get('count','?')) if isinstance(d,dict) else '?'
        report.add_metric('Total contatos CRM', total)
except Exception as e:
    report.add_warning(f'CRM: {e}')
path = report.save(); report.print_summary(); print(f'[social_analytics] Concluído: {path}')
""")
    print(f'[scheduler-boot] social_analytics.py criado')

print(f'[scheduler-boot] custom scripts verificados em {CUSTOM}')
print(f'[scheduler-boot] scripts: {[f.name for f in CUSTOM.glob("*.py")]}')
''')
print('[boot] scheduler-boot.py escrito em /workspace/config/')

# --- 9. Ensure oracle and levi use sonnet (not haiku) to avoid context overflow ---
for _agent_name in ['oracle', 'levi-atendimento']:
    _agent_md = pathlib.Path(f'/workspace/.claude/agents/{_agent_name}.md')
    if _agent_md.exists():
        _agent_text = _agent_md.read_text()
        if 'model: haiku' in _agent_text:
            _agent_md.write_text(_agent_text.replace('model: haiku', 'model: sonnet'))
            print(f'[boot] {_agent_name}.md: model atualizado haiku -> sonnet')
        else:
            print(f'[boot] {_agent_name}.md: modelo ja correto')
    else:
        print(f'[boot] {_agent_name}.md nao encontrado, pulando patch')

# --- 10. Patch app.py: _try_api_token_auth accepts ?token= query param (for webhooks) ---
_app_py = pathlib.Path('/workspace/dashboard/backend/app.py')
if _app_py.exists():
    _app_text = _app_py.read_text()
    _old_auth = ('    header = request.headers.get("Authorization", "")\n'
                 '    if not header.startswith("Bearer "):\n'
                 '        return False\n'
                 '    provided = header[len("Bearer "):].strip()')
    _new_auth = ('    header = request.headers.get("Authorization", "")\n'
                 '    if header.startswith("Bearer "):\n'
                 '        provided = header[len("Bearer "):].strip()\n'
                 '    else:\n'
                 '        provided = (request.args.get("token") or "").strip()')
    if _old_auth in _app_text:
        _app_py.write_text(_app_text.replace(_old_auth, _new_auth))
        # Clear stale pyc
        for _pyc in pathlib.Path('/workspace/dashboard/backend/__pycache__').glob('app*.pyc'):
            _pyc.unlink(missing_ok=True)
        print('[boot] app.py: _try_api_token_auth patched para aceitar ?token= query param')
    elif _new_auth in _app_text:
        print('[boot] app.py: _try_api_token_auth ja patched')
    else:
        print('[boot] app.py: padrao nao encontrado em _try_api_token_auth, pulando patch')

# --- 10b. Patch routes/heartbeats.py: add public webhook trigger endpoint ---
_hb_py = pathlib.Path('/workspace/dashboard/backend/routes/heartbeats.py')
if _hb_py.exists():
    _hb_text = _hb_py.read_text()
    _webhook_marker = 'def webhook_heartbeat_trigger'
    if _webhook_marker not in _hb_text:
        _hb_text += '''

# ── Public Webhook Trigger (Evolution Go → Levi) ──────────────────────────
@bp.route("/api/triggers/webhook/heartbeat/<string:heartbeat_id>", methods=["POST"])
def webhook_heartbeat_trigger(heartbeat_id):
    """External webhook endpoint for Evolution Go to trigger a heartbeat immediately."""
    import os as _os, secrets as _sec
    expected = _os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    provided = (request.args.get("token") or request.headers.get("X-Webhook-Token") or "").strip()
    if not expected or not provided or not _sec.compare_digest(provided, expected):
        return jsonify({"status": "ok"}), 200  # silent reject
    hb = Heartbeat.query.get(heartbeat_id)
    if not hb or not hb.enabled:
        return jsonify({"status": "ok", "skipped": "disabled"}), 200
    from heartbeat_dispatcher import dispatch
    dispatched, run_id = dispatch(heartbeat_id, "webhook")
    return jsonify({"status": "ok", "dispatched": dispatched, "run_id": run_id}), 200
'''
        _hb_py.write_text(_hb_text)
        for _pyc in pathlib.Path('/workspace/dashboard/backend/__pycache__').glob('routes*heartbeats*.pyc'):
            _pyc.unlink(missing_ok=True)
        print('[boot] routes/heartbeats.py: webhook_heartbeat_trigger adicionado')
    else:
        print('[boot] routes/heartbeats.py: webhook_heartbeat_trigger ja presente')

# --- 11. Ensure DASHBOARD_API_TOKEN is set in /workspace/.env (load_dotenv reads this) ---
# The container's .env.nexus sets the token, but /workspace/.env (bind-mounted from image)
# may have an empty DASHBOARD_API_TOKEN= line that load_dotenv would keep as empty (already-set
# env var wins only if dotenv sees a non-empty existing os.environ entry; on first boot
# the PID 1 env may not have the value if the container was started before .env.nexus was updated).
# Solution: always sync DASHBOARD_API_TOKEN from os.environ into /workspace/.env at boot.
# Also force PYTHONUNBUFFERED=1 so Flask/dispatcher logs appear immediately in docker logs.
_env_file = pathlib.Path('/workspace/.env')
if _env_file.exists():
    import re as _re
    _env_text = _env_file.read_text()
    _runtime_token = os.environ.get('DASHBOARD_API_TOKEN', '').strip()
    _env_changed = False
    if _runtime_token:
        _env_text2 = _re.sub(
            r'(?m)^DASHBOARD_API_TOKEN=.*$',
            f'DASHBOARD_API_TOKEN={_runtime_token}',
            _env_text
        )
        if _env_text2 != _env_text:
            _env_text = _env_text2
            _env_changed = True
            print(f'[boot] /workspace/.env: DASHBOARD_API_TOKEN sincronizado ({_runtime_token[:8]}...)')
        else:
            print('[boot] /workspace/.env: DASHBOARD_API_TOKEN ja correto')
    else:
        print('[boot] AVISO: DASHBOARD_API_TOKEN nao encontrado no ambiente, .env nao atualizado')
    # Ensure PYTHONUNBUFFERED=1 so logs flush immediately to docker logs (avoids buffering in pipes)
    if 'PYTHONUNBUFFERED=' not in _env_text:
        _env_text = _env_text.rstrip('\n') + '\nPYTHONUNBUFFERED=1\n'
        _env_changed = True
        print('[boot] /workspace/.env: PYTHONUNBUFFERED=1 adicionado')
    if _env_changed:
        _env_file.write_text(_env_text)
    # Also set in current process environment so bootscript-spawned processes inherit it
    os.environ['PYTHONUNBUFFERED'] = '1'

# --- 11. Fix /root/.claude/settings.json: disable skipDangerousModePermissionPrompt + clear session cache ---
_root_settings = pathlib.Path('/root/.claude/settings.json')
try:
    _rs = json.loads(_root_settings.read_text()) if _root_settings.exists() else {}
    _changed_rs = False
    # Remove skipDangerousModePermissionPrompt — openclaude adds --dangerously-skip-permissions
    # when this is true, which fails as root even with IS_SANDBOX=1
    if _rs.pop('skipDangerousModePermissionPrompt', None) is not None:
        _changed_rs = True
    # Remove any saved Codex provider profile that causes "Codex auth required" warnings
    if _rs.pop('oauthAccount', None) is not None:
        _changed_rs = True
    if _rs.pop('activeProvider', None) is not None:
        _changed_rs = True
    if _changed_rs:
        _root_settings.write_text(json.dumps(_rs, indent=2))
        print('[boot] /root/.claude/settings.json: cleaned skipDangerousModePermissionPrompt + codex profile')
    else:
        print('[boot] /root/.claude/settings.json: already clean')
except Exception as _e:
    print(f'[boot] /root/.claude/settings.json: erro ao patchear: {_e}')

# Clear stale openclaude session cache (causes "Codex auth required" on reuse of old sessions)
_sessions_dir = pathlib.Path('/root/.claude/sessions')
if _sessions_dir.exists():
    import shutil as _shutil
    try:
        _shutil.rmtree(str(_sessions_dir))
        _sessions_dir.mkdir()
        print('[boot] /root/.claude/sessions: cache limpo (evita erro Codex auth)')
    except Exception as _e:
        print(f'[boot] /root/.claude/sessions: erro ao limpar: {_e}')

# --- 12. Patch providers.json: escolher melhor provider disponível para o terminal ---
# Prioridade: OpenRouter (tem crédito garantido) → OpenAI (se tiver crédito)
# Motivo: OpenAI pode estar sem crédito; OpenRouter é o provider confiável
_providers_json = pathlib.Path('/workspace/config/providers.json')
if _providers_json.exists():
    try:
        _pj = json.load(open(_providers_json))
        _openrouter_key = os.environ.get('OPENROUTER_API_KEY', '').strip()
        _changed_pj = False

        # POLÍTICA: apenas OpenRouter com modelos FREE é autorizado.
        # OpenAI direct e Anthropic direct NÃO são usados (serviços pagos).
        _FREE_MODEL = 'google/gemma-4-26b-a4b-it:free'
        _target = 'openrouter'

        if _pj.get('active_provider') != _target:
            _pj['active_provider'] = _target
            _changed_pj = True

        # Garantir que openrouter está configurado com modelo free funcional
        _pj.setdefault('providers', {}).setdefault('openrouter', {})
        _or = _pj['providers']['openrouter']
        _orev = _or.get('env_vars', {})
        _or_need = {
            'CLAUDE_CODE_USE_OPENAI': '1',
            'OPENAI_BASE_URL': 'https://openrouter.ai/api/v1',
            'OPENAI_API_KEY': _openrouter_key or _orev.get('OPENAI_API_KEY', ''),
            'OPENAI_MODEL': _FREE_MODEL,
            'CLAUDE_CODE_PROVIDER_PROFILE_ENV_APPLIED': '1',
        }
        for k, v in _or_need.items():
            if v and _orev.get(k) != v:
                _orev[k] = v
                _changed_pj = True
        _or['env_vars'] = _orev
        _or['default_model'] = _FREE_MODEL

        if _changed_pj:
            json.dump(_pj, open(_providers_json, 'w'), indent=2, ensure_ascii=False)
            print(f'[boot] providers.json: active_provider={_target} model={_FREE_MODEL}')
        else:
            print(f'[boot] providers.json: ja configurado (active={_pj.get("active_provider")})')
    except Exception as _e:
        print(f'[boot] providers.json: erro ao patchear: {_e}')
else:
    print('[boot] providers.json: arquivo nao encontrado, pulando patch')

# --- 13b. Patch openclaude model metadata: add free models so context window is known ---
# Without this, openclaude warns "model not in integration model metadata" for gemma/nemotron free
# and uses conservative 128k, sometimes causing context compaction failures.
for _oc_file in ['/usr/lib/node_modules/@gitlawb/openclaude/dist/cli.mjs',
                  '/usr/lib/node_modules/@gitlawb/openclaude/dist/sdk.mjs']:
    _oc_path = pathlib.Path(_oc_file)
    if _oc_path.exists():
        _oc_text = _oc_path.read_text()
        _gemma4_entry = '["google/gemma-4-26b-a4b-it:free", "Google Gemma 4 26B (Free)", 131072, 8192]'
        _nemotron_entry = '["nvidia/nemotron-3-super-120b-a12b:free", "NVIDIA Nemotron 3 Super 120B (Free)", 131072, 32768]'
        _anchor = '["google/gemma-3-27b-it", "Google Gemma 3 27B IT", 131072, 16384]'
        if _anchor in _oc_text and _gemma4_entry not in _oc_text:
            _replacement = f'{_gemma4_entry},\n    {_nemotron_entry},\n    {_anchor}'
            _oc_path.write_text(_oc_text.replace(_anchor, _replacement))
            print(f'[boot] {_oc_path.name}: gemma-4-26b + nemotron-120b free adicionados ao model metadata')
        elif _gemma4_entry in _oc_text:
            print(f'[boot] {_oc_path.name}: free models ja presentes no model metadata')
        else:
            print(f'[boot] {_oc_path.name}: anchor nao encontrado, pulando patch')
    else:
        print(f'[boot] {_oc_file}: nao encontrado')

# --- 13. Patch workspace.yaml: add CRM API config so Clawdia knows correct endpoints ---
# Chatwoot API uses:  api_access_token: <token>  (not Bearer)
# Correct endpoints:  /api/v1/accounts/{account_id}/conversations
#                     /api/v1/accounts/{account_id}/contacts
#                     /api/v1/accounts/{account_id}/contacts/search?q=<termo>
_ws_yaml = pathlib.Path('/workspace/config/workspace.yaml')
if _ws_yaml.exists():
    try:
        _ws_content = _ws_yaml.read_text()
        _crm_url = os.environ.get('EVO_CRM_URL', 'http://evo-crm:3000')
        _crm_block = f'''
crm:
  url: {_crm_url}
  # Header de autenticacao: api_access_token (NAO Bearer)
  # Exemplo: curl -H "api_access_token: <token>" http://evo-crm:3000/api/v1/contacts
  api_header: api_access_token
  api_token_env: EVO_CRM_API_TOKEN
  endpoints:
    profile:          /api/v1/profile
    contacts:         /api/v1/contacts
    contact_search:   /api/v1/contacts/search?q={{query}}
    conversations:    /api/v1/conversations
    conversation:     /api/v1/conversations/{{id}}
    messages:         /api/v1/conversations/{{id}}/messages
    send_message:     /api/v1/conversations/{{id}}/messages  (POST)
    inboxes:          /api/v1/inboxes
    labels:           /api/v1/labels
    teams:            /api/v1/teams
  # Paginacao: ?page=1  (padrao 20 itens por pagina)
  # Status de conversa: open, resolved, pending
  # Exemplo filtrar conversas abertas: /api/v1/conversations?status=open
'''
        if 'crm:' not in _ws_content:
            _ws_yaml.write_text(_ws_content.rstrip() + '\n' + _crm_block)
            print('[boot] workspace.yaml: CRM config (Chatwoot API) adicionado')
        else:
            print('[boot] workspace.yaml: CRM config ja presente')
    except Exception as _e:
        print(f'[boot] workspace.yaml: erro ao patchear: {_e}')
else:
    print('[boot] workspace.yaml: nao encontrado, pulando patch 13')

# --- 14. Fix TPM rate limit: reduce Levi heartbeat interval 60s → 120s, max_turns 25 → 15 ---
# Levi runs every 60s with ~28k-token prompts = ~28k TPM per run = ~28000*60/60 = 28k/min
# Doubling the interval to 120s halves TPM consumption. max_turns 15 vs 25 = ~40% less tokens/run.
_db_path = pathlib.Path('/workspace/dashboard/data/evonexus.db')
if _db_path.exists():
    try:
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(str(_db_path))
        _cur = _conn.cursor()
        # Check current values first
        _cur.execute("SELECT id, interval_seconds, max_turns FROM heartbeats WHERE id LIKE '%levi%'")
        _levi_rows = _cur.fetchall()
        _tpm_changed = False
        for _hb_id, _iv, _mt in _levi_rows:
            if _iv is not None and _iv < 120:
                _cur.execute("UPDATE heartbeats SET interval_seconds = 120 WHERE id = ?", (_hb_id,))
                print(f'[boot] {_hb_id}: interval {_iv}s → 120s (TPM rate limit fix)')
                _tpm_changed = True
            if _mt is None or _mt > 15:
                _cur.execute("UPDATE heartbeats SET max_turns = 15 WHERE id = ?", (_hb_id,))
                print(f'[boot] {_hb_id}: max_turns {_mt} → 15 (TPM rate limit fix)')
                _tpm_changed = True
        if not _levi_rows:
            print('[boot] levi heartbeat nao encontrado no DB, pulando patch 14')
        elif not _tpm_changed:
            print('[boot] levi heartbeat: interval >= 120s + max_turns <= 15, ja correto')
        _conn.commit()
        _conn.close()
    except Exception as _e:
        print(f'[boot] levi heartbeat TPM patch: {_e}')
else:
    print('[boot] evonexus.db nao encontrado, pulando patch 14 (DB sera criado pelo dashboard)')

# --- 15. Create /mnt/skills/user symlink → /workspace/.claude/skills ---
_mnt_skills = pathlib.Path('/mnt/skills')
_mnt_skills.mkdir(parents=True, exist_ok=True)
_user_link = _mnt_skills / 'user'
if not _user_link.exists():
    import os as _os2
    _os2.symlink('/workspace/.claude/skills', str(_user_link))
    print('[boot] /mnt/skills/user → /workspace/.claude/skills criado')
elif not _user_link.is_symlink():
    print('[boot] /mnt/skills/user existe mas nao e symlink — pulando')
else:
    print('[boot] /mnt/skills/user symlink ja existe')

# --- 16. Ensure longevos-recepcao heartbeat exists (persists across volume recreations) ---
if _db_path.exists():
    try:
        import sqlite3 as _sqlite3_lv
        _conn_lv = _sqlite3_lv.connect(str(_db_path))
        _longevos_prompt = '''Voce e longevos-recepcao, especialista em recepcao da clinica Longevos Saude. MODO AUTOMATICO.

Seu protocolo completo de atendimento esta no seu arquivo de identidade (ja carregado no contexto). Use-o para saber como conduzir cada conversa com empatia e acolhimento.

=== PIPELINE LONGEVOS SAUDE ===
Pipeline ID: 7e9f7f4c-69da-4ea4-8ce2-325a3768f63b
Estagios:
  1. Novo Contato:      9ba3bf48-4b7e-40c1-a703-b9b3b43ffb19
  2. Acolhimento:       05474b3a-1568-4efc-a2ef-0868a0a5426c
  3. Mapeando Objetivo: 5c4ba59f-712d-4272-be1f-0dac9c2a225b
  4. Quebrando Objecoes:695f8a27-b8e3-4197-b464-1d048a5bffb6
  5. Pronto p Agendar:  21ce3e66-9c32-4539-aba5-4f6c4fa72c0f
  6. Agendamento:       5af44c5e-ce7c-442c-8e18-91ddafa93b72
  7. Em Recuperacao:    1b2e7f1d-89a7-4d67-b1e2-4e4988cb6078
  8. Consulta Agendada: bc11bbf4-d879-4263-b997-31f8d03b2bf7
  9. Nao Compareceu:    aa5ef077-3f45-403f-8634-4f59d2056291
 10. Abandonado:        f8b36291-cfb1-4cde-9316-97d86c34e64a

=== WORKFLOW ===

PASSO 1 - Buscar itens ativos no pipeline Longevos:
python3 /mnt/skills/user/int-evo-crm/scripts/evo_crm_client.py pipeline_items 7e9f7f4c-69da-4ea4-8ce2-325a3768f63b
Filtrar: itens com conversation.status="open" e conversation.waiting_since != null.
Ignorar: Abandonado (f8b36291), Consulta Agendada (bc11bbf4).
Max 2 itens para processar.
Se nenhum: retornar {"action":"skip","reason":"sem pacientes aguardando Longevos","conversations_processed":0,"stages_advanced":0}

PASSO 2 - Para cada item (max 2):

  [A] ANTI-SPAM - Verificar ultima mensagem:
  curl -s -H "api_access_token: d069b81fe3ec21e9413a68da97202ac5c5d9d0e89000504a82602761dc8cdb31" "http://evo-crm:3000/api/v1/conversations/<CONV_ID>/messages?page=1"
  SE ultima msg = outgoing ou AgentBot: PULE. Aguardar resposta do cliente.
  SE message_type=incoming: continuar.

  [B] AVANCAR ESTAGIO se cliente forneceu informacao relevante:
  Analisar historico: o que o cliente ja disse?
  - Se no Novo Contato e cliente ja respondeu: mover para Acolhimento
  - Se em Acolhimento e cliente expressou objetivo de saude: mover para Mapeando Objetivo
  - Se mapeando objetivo e cliente descreveu situacao: mover para Quebrando Objecoes
  - Se cliente mostra resistencia/duvida (preco, tempo): permanecer em Quebrando Objecoes
  - Se cliente quer agendar: mover para Pronto p Agendar
  - Se data/hora confirmada: mover para Agendamento depois Consulta Agendada
  python3 /mnt/skills/user/int-evo-crm/scripts/evo_crm_client.py move_item 7e9f7f4c-69da-4ea4-8ce2-325a3768f63b <ITEM_ID> --stage_id <NOVO_STAGE_ID>

  [C] MONTAR RESPOSTA - UMA unica mensagem empatica e acolhedora:
  Usar seu protocolo de identidade para o estagio atual.
  Tom: carinhoso, profissional, focado na saude e bem-estar do cliente.
  Estagios guia:
  - Novo Contato/Acolhimento: apresentar a clinica, empatia inicial, perguntar objetivo de saude
  - Mapeando Objetivo: explorar objetivo especifico (emagrecimento, hormonios, longevidade)
  - Quebrando Objecoes: validar preocupacoes, apresentar beneficios, casos de sucesso
  - Pronto p Agendar: propor opcoes de data/hora para consulta inicial
  - Agendamento: confirmar data/hora, passar endereco/instrucoes
  - Em Recuperacao: mensagem calorosa reengajando o interesse

  [D] ENVIAR mensagem (phone SEM o +):
  curl -s -X POST http://evolution-api:8080/message/sendText/teste -H "apikey: 13843d97a33afda28fdd04919c07ce7237c50d5bc93f235979cfd84af6ac0fd0" -H "Content-Type: application/json" -d "{\"number\":\"<PHONE_SEM_+>\",\"text\":\"<MENSAGEM>\"}"

  [E] TICKET NEXUS:
  curl -s "http://localhost:8080/api/tickets?status=open" -H "Authorization: Bearer 07e118a12aa32b9c29f100a4132d4855601196ee85b7dfdf3482724a663318be"
  SE nao existe ticket para esta conversa:
    curl -s -X POST "http://localhost:8080/api/tickets" -H "Content-Type: application/json" -H "Authorization: Bearer 07e118a12aa32b9c29f100a4132d4855601196ee85b7dfdf3482724a663318be" -d "{\"title\":\"Longevos #<DISPLAY_ID>: <CONTACT_NAME>\",\"description\":\"clinica=Longevos estagio=<STAGE_NAME> conv=<CONV_ID> phone=<PHONE>\",\"priority\":\"medium\",\"assignee_agent\":\"longevos-recepcao\",\"source_agent\":\"longevos-recepcao\"}"
  SE ja existe: comentar:
    curl -s -X POST "http://localhost:8080/api/tickets/<TICKET_ID>/comments" -H "Content-Type: application/json" -H "Authorization: Bearer 07e118a12aa32b9c29f100a4132d4855601196ee85b7dfdf3482724a663318be" -d "{\"body\":\"Longevos-recepcao: estagio=<STAGE> msg=<RESUMO>\"}"

=== REGRAS ===
1. UMA mensagem por conversa por execucao
2. Skip se ultima msg = outgoing
3. Phone sem + inicial
4. Criar/atualizar ticket Nexus sempre

Resposta JSON:
{"action":"work","reason":"atendeu X pacientes Longevos Saude","conversations_processed":0,"stages_advanced":0}'''

        _existing_lv = _conn_lv.execute(
            "SELECT id, length(decision_prompt) FROM heartbeats WHERE id='longevos-recepcao'"
        ).fetchone()
        _now_lv = int(__import__('time').time())

        if _existing_lv is None:
            _conn_lv.execute(
                """INSERT INTO heartbeats
                   (id, agent, interval_seconds, max_turns, timeout_seconds, lock_timeout_seconds,
                    wake_triggers, enabled, goal_id, required_secrets, decision_prompt, source_plugin,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('longevos-recepcao', 'longevos-recepcao', 900, 10, 600, 600,
                 '["interval","manual"]', 1, None, None, _longevos_prompt, None, _now_lv, _now_lv)
            )
            print('[boot] longevos-recepcao: heartbeat CRIADO no banco')
        elif _existing_lv[1] < 500:
            _conn_lv.execute(
                "UPDATE heartbeats SET decision_prompt=?, updated_at=? WHERE id='longevos-recepcao'",
                (_longevos_prompt, _now_lv)
            )
            print('[boot] longevos-recepcao: prompt atualizado (era stub)')
        else:
            print(f'[boot] longevos-recepcao: ja existe com prompt real ({_existing_lv[1]}c)')

        _conn_lv.commit()
        _conn_lv.close()
    except Exception as _e_lv:
        print(f'[boot] longevos-recepcao setup: {_e_lv}')
else:
    print('[boot] longevos-recepcao: DB ainda nao existe, sera criado na primeira execucao')

# --- 17. Boot-time stale lock cleanup + guardian lock_timeout sanity ---
if _db_path.exists():
    try:
        import sqlite3 as _sqlite3_boot
        _conn_boot = _sqlite3_boot.connect(str(_db_path))
        _conn_boot.row_factory = _sqlite3_boot.Row

        # 17a. Clear heartbeat_runs stuck as 'running' beyond their lock_timeout_seconds
        _stale_heartbeats = _conn_boot.execute(
            "SELECT id, lock_timeout_seconds FROM heartbeats WHERE lock_timeout_seconds IS NOT NULL"
        ).fetchall()
        _total_stale = 0
        _now_boot = __import__('datetime').datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000000Z')
        for _hb in _stale_heartbeats:
            _hid = _hb['id']
            _lts = _hb['lock_timeout_seconds']
            _res = _conn_boot.execute(
                """UPDATE heartbeat_runs
                   SET status='fail', ended_at=?, error='Stale lock cleared on boot'
                   WHERE heartbeat_id=? AND status='running'
                     AND replace(replace(started_at,'T',' '),'Z','') < datetime('now', ? || ' seconds')""",
                (_now_boot, _hid, f'-{_lts}')
            )
            if _res.rowcount:
                print(f'[boot] stale lock cleanup: {_hid} cleared {_res.rowcount} stale run(s)')
                _total_stale += _res.rowcount
        if _total_stale == 0:
            print('[boot] stale lock cleanup: nenhum lock stale encontrado')

        # 17b. Ensure guardian-5m lock_timeout_seconds >= timeout_seconds (prevents stacking)
        _g = _conn_boot.execute(
            "SELECT timeout_seconds, lock_timeout_seconds FROM heartbeats WHERE id='guardian-5m'"
        ).fetchone()
        if _g and _g['lock_timeout_seconds'] < _g['timeout_seconds'] + 50:
            _new_lts = _g['timeout_seconds'] + 50
            _conn_boot.execute(
                "UPDATE heartbeats SET lock_timeout_seconds=? WHERE id='guardian-5m'", (_new_lts,)
            )
            print(f"[boot] guardian-5m: lock_timeout_seconds corrigido para {_new_lts}")
        elif _g:
            print(f"[boot] guardian-5m: lock_timeout_seconds={_g['lock_timeout_seconds']} OK")

        _conn_boot.commit()
        _conn_boot.close()
    except Exception as _e_boot:
        print(f'[boot] patch 17 stale lock cleanup: {_e_boot}')
else:
    print('[boot] patch 17: DB ainda nao existe, pulando stale lock cleanup')

# --- 18. Patch heartbeat_dispatcher.py: add lock check to prevent concurrent runs ---
_dispatcher_path = pathlib.Path('/workspace/dashboard/backend/heartbeat_dispatcher.py')
if _dispatcher_path.exists():
    _disp_text = _dispatcher_path.read_text()
    _disp_anchor = '    # Debounce check\n    debounced, existing_id = _is_debounced(heartbeat_id)'
    _disp_lock_check = '''    # Lock check: skip if a run is still active within lock_timeout window
    conn2 = _get_db()
    try:
        hb_cfg = conn2.execute(
            "SELECT lock_timeout_seconds FROM heartbeats WHERE id = ?", (heartbeat_id,)
        ).fetchone()
        if hb_cfg and hb_cfg["lock_timeout_seconds"]:
            _lts = hb_cfg["lock_timeout_seconds"]
            active = conn2.execute(
                """SELECT run_id FROM heartbeat_runs
                   WHERE heartbeat_id = ? AND status = 'running'
                     AND replace(replace(started_at,'T',' '),'Z','') > datetime('now', '-' || ? || ' seconds')
                   LIMIT 1""",
                (heartbeat_id, _lts),
            ).fetchone()
            if active:
                print(f"[dispatcher] {heartbeat_id} locked (run {active['run_id']} still active), skipping", flush=True)
                return False, None
    finally:
        conn2.close()

    # Debounce check
    debounced, existing_id = _is_debounced(heartbeat_id)'''
    if _disp_anchor in _disp_text and _disp_lock_check not in _disp_text:
        _dispatcher_path.write_text(_disp_text.replace(_disp_anchor, _disp_lock_check))
        print('[boot] heartbeat_dispatcher.py: lock check adicionado')
    elif _disp_lock_check in _disp_text:
        print('[boot] heartbeat_dispatcher.py: lock check ja presente')
    else:
        print('[boot] heartbeat_dispatcher.py: ancora nao encontrada, pulando patch 18')
else:
    print('[boot] heartbeat_dispatcher.py nao encontrado, pulando patch 18')

# --- 19. Patch MemPalace.tsx: fix variable shadowing (t) and add key props to tab sections ---
_mempalace_src = pathlib.Path('/workspace/dashboard/frontend/src/pages/MemPalace.tsx')
if _mempalace_src.exists():
    _mp_text = _mempalace_src.read_text()
    _mp_changed = False

    # Fix 1: rename 't' loop variable in tabs.map to 'tabItem' to avoid shadowing useTranslation's 't'
    if 'tabs.map((t) =>' in _mp_text:
        _mp_text = _mp_text.replace('tabs.map((t) =>', 'tabs.map((tabItem) =>')
        _mp_text = _mp_text.replace('key={t.key}', 'key={tabItem.key}')
        _mp_text = _mp_text.replace('onClick={() => setTab(t.key)}', 'onClick={() => setTab(tabItem.key)}')
        _mp_text = _mp_text.replace('tab === t.key', 'tab === tabItem.key')
        _mp_text = _mp_text.replace('{t.label}', '{tabItem.label}')
        _mp_changed = True
        print('[boot] MemPalace.tsx: tabs.map variable shadowing corrigido')

    # Fix 2: add stable key props to each tab section root div to prevent React InsertBefore errors
    if 'key="tab-status"' not in _mp_text:
        import re as _re
        # After '{tab === 'status' && (' find the first <div className="space-y-6"> and add key
        _mp_text = _re.sub(
            r"(\{tab === 'status' && \(\s*)<div className=\"space-y-6\">",
            r'\1<div key="tab-status" className="space-y-6">',
            _mp_text, count=1
        )
        _mp_text = _re.sub(
            r"(\{tab === 'sources' && \(\s*)<div className=\"space-y-6\">",
            r'\1<div key="tab-sources" className="space-y-6">',
            _mp_text, count=1
        )
        _mp_text = _re.sub(
            r"(\{tab === 'search' && \(\s*)<div className=\"space-y-6\">",
            r'\1<div key="tab-search" className="space-y-6">',
            _mp_text, count=1
        )
        _mp_changed = True
        print('[boot] MemPalace.tsx: key props adicionados nas secoes de tab')

    if _mp_changed:
        _mempalace_src.write_text(_mp_text)
        print('[boot] MemPalace.tsx: arquivo atualizado')
    else:
        print('[boot] MemPalace.tsx: ja corrigido, sem alteracoes')

    # --- Rebuild frontend if MemPalace source was changed ---
    _frontend_dir = pathlib.Path('/workspace/dashboard/frontend')
    _node_modules = _frontend_dir / 'node_modules'
    _dist_dir = _frontend_dir / 'dist'
    if _mp_changed:
        print('[boot] Reconstruindo frontend apos patch MemPalace...')
        import subprocess as _sp
        try:
            if not _node_modules.exists():
                print('[boot] Instalando node_modules...')
                _r = _sp.run(['npm', 'install'], cwd=str(_frontend_dir), capture_output=True, text=True, timeout=300)
                print(f'[boot] npm install: {_r.returncode}')
            _vite = str(_node_modules / '.bin' / 'vite')
            _r2 = _sp.run([_vite, 'build'], cwd=str(_frontend_dir), capture_output=True, text=True, timeout=180)
            if _r2.returncode == 0:
                print('[boot] Frontend reconstruido com sucesso')
            else:
                print(f'[boot] Erro no build: {_r2.stderr[-500:]}')
        except Exception as _e_build:
            print(f'[boot] Erro no build frontend: {_e_build}')
    else:
        print('[boot] Frontend: sem mudancas no MemPalace, rebuild desnecessario')
else:
    print('[boot] MemPalace.tsx nao encontrado, pulando patch 19')

# --- 20. Install gog CLI (Google OAuth Gateway for calendar/gmail/tasks skills) ---
# gog is not a published npm/pip package — it is a custom Python CLI stored in
# /workspace/.claude/bin/gog (nexus_claude persistent volume). On each boot we
# ensure the binary is copied to /usr/local/bin/gog so it is on PATH.
_gog_src  = pathlib.Path('/workspace/.claude/bin/gog')
_gog_dest = pathlib.Path('/usr/local/bin/gog')

if _gog_src.exists():
    # Ensure executable bit on source
    import stat as _stat
    _gog_src.chmod(_gog_src.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
    # Copy to /usr/local/bin on every boot (container tmpfs resets between restarts)
    import shutil as _shutil
    _shutil.copy2(str(_gog_src), str(_gog_dest))
    _gog_dest.chmod(_gog_dest.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
    print('[boot] gog: copiado /workspace/.claude/bin/gog -> /usr/local/bin/gog')
    # Quick smoke test
    import subprocess as _gog_sp
    _gt = _gog_sp.run([str(_gog_dest), 'calendar', 'events', '--today', '--json'],
                      capture_output=True, text=True, timeout=10)
    if _gt.returncode == 0:
        print(f'[boot] gog: smoke test ok (output: {_gt.stdout.strip()[:40]}...)')
    else:
        print(f'[boot] gog: smoke test falhou rc={_gt.returncode}: {_gt.stderr[:80]}')
else:
    print('[boot] gog: /workspace/bin/gog nao encontrado — pulando patch 20')
    print('[boot] gog: Reinstale com: docker cp gog_cli.py <container>://workspace/bin/gog')

print('[boot] nexus-boot.py concluido')

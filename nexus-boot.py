#!/usr/bin/env python3
"""Boot-time patches for evo-nexus-dashboard container."""
import json, os, pathlib, shutil, subprocess

# --- 0. Install system dependencies missing from image ---
if not shutil.which('make'):
    subprocess.run(['apt-get', 'install', '-y', '-qq', 'make'], capture_output=True)
    print('[boot] make instalado')
else:
    print('[boot] make ja disponivel')

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

    # Patch 5: add --allowedTools + --bare to enable autonomous tool use without permission issues
    old5 = '        "--print",\n        "--max-turns", str(max_turns),\n'
    new5 = '        "--print",\n        "--allowedTools", "Bash(*) Read(*) Write(*) Edit(*) Skill(*) Agent(*)",\n        "--bare",\n        "--max-turns", str(max_turns),\n'

    # Patch 6: inject OPENROUTER_API_KEY as OPENAI_API_KEY only for the Claude subprocess,
    # avoiding conflict with the container-level OPENAI_API_KEY used by Whisper transcription.
    old6 = ('        proc = subprocess.Popen(\n'
            '            cmd,\n'
            '            stdin=subprocess.PIPE,\n'
            '            stdout=subprocess.PIPE,\n'
            '            stderr=subprocess.PIPE,\n'
            '            text=True,\n'
            '            cwd=str(WORKSPACE),\n'
            '            start_new_session=True,  # new process group for clean kill\n'
            '        )')
    new6 = ('        _claude_env = dict(os.environ)\n'
            '        if os.environ.get("CLAUDE_CODE_USE_OPENAI") == "1" and os.environ.get("OPENROUTER_API_KEY"):\n'
            '            _claude_env["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]\n'
            '        proc = subprocess.Popen(\n'
            '            cmd,\n'
            '            stdin=subprocess.PIPE,\n'
            '            stdout=subprocess.PIPE,\n'
            '            stderr=subprocess.PIPE,\n'
            '            text=True,\n'
            '            env=_claude_env,\n'
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

    for old, new in [(old1, new1), (old2, new2), (old3, new3), (old4, new4), (old5, new5), (old6, new6), (old7, new7)]:
        if old in t:
            t = t.replace(old, new)
            changed += 1

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
    print(f'[boot] heartbeat_runner.py patched ({changed}/7 fixes applied)')

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
_env_file = pathlib.Path('/workspace/.env')
if _env_file.exists():
    import re as _re
    _env_text = _env_file.read_text()
    _runtime_token = os.environ.get('DASHBOARD_API_TOKEN', '').strip()
    if _runtime_token:
        _env_text2 = _re.sub(
            r'(?m)^DASHBOARD_API_TOKEN=.*$',
            f'DASHBOARD_API_TOKEN={_runtime_token}',
            _env_text
        )
        if _env_text2 != _env_text:
            _env_file.write_text(_env_text2)
            print(f'[boot] /workspace/.env: DASHBOARD_API_TOKEN sincronizado ({_runtime_token[:8]}...)')
        else:
            print('[boot] /workspace/.env: DASHBOARD_API_TOKEN ja correto')
    else:
        print('[boot] AVISO: DASHBOARD_API_TOKEN nao encontrado no ambiente, .env nao atualizado')

print('[boot] nexus-boot.py concluido')

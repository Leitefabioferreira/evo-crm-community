# EvoNexus — Guia de Deploy na VPS

## Pré-requisitos
- Docker + Docker Compose instalados
- Stack principal (`docker-compose.yml`) já rodando na VPS
- Rede `evo-crm-community_default` existente (criada pela stack principal)

## 1. Clonar / atualizar arquivos

```bash
cd /opt/evo-crm-community   # ou onde estiver na VPS
git pull origin main         # ou copiar os arquivos manualmente
```

Arquivos necessários no diretório:
```
docker-compose.nexus.yml    ← compose do Nexus
nexus-boot.py               ← montado read-only no container
nexus-nginx.conf            ← config nginx do Nexus
.env.nexus                  ← secrets (NÃO commitar!)
```

## 2. Criar o .env.nexus na VPS

```bash
cp .env.nexus.example .env.nexus
nano .env.nexus  # preencher os valores reais
```

Variáveis obrigatórias:
| Variável | Descrição |
|----------|-----------|
| `EVONEXUS_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `DASHBOARD_API_TOKEN` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `EVO_CRM_API_TOKEN` | UUID do token do Chatwoot |
| `EVOLUTION_API_KEY` | API key da Evolution API |
| `OPENAI_API_KEY` | `sk-proj-...` (OpenAI direto) |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` (fallback) |

Variáveis opcionais:
| Variável | Descrição |
|----------|-----------|
| `ANTHROPIC_API_KEY` | Deixar vazio por enquanto; sistema faz fallback automático |

## 3. Configurar ANTHROPIC_API_KEY no ambiente (opcional)

Se quiser usar Claude como provider, exporte no ambiente do servidor:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
# OU adicionar ao .env.nexus diretamente
```

## 4. Subir os containers

```bash
docker compose -f docker-compose.nexus.yml up -d
```

Aguardar ~60-90s para o healthcheck passar. Verificar:
```bash
docker compose -f docker-compose.nexus.yml ps
# Todos devem mostrar "healthy" ou "running"
```

## 5. Verificar boot

```bash
docker logs evo-crm-community-evo-nexus-dashboard-1 2>&1 | grep '\[boot\]'
```

Saída esperada:
```
[boot] heartbeat_runner.py patched (7/7 fixes applied)
[boot] /root/.claude/settings.json: already clean
[boot] providers.json: active_provider atualizado para openai/gpt-4o-mini
[boot] nexus-boot.py concluido
```

## 6. Verificar heartbeats

```bash
docker logs evo-crm-community-evo-nexus-dashboard-1 2>&1 | grep '\[provider\]\|\[runner\].*DONE'
```

Esperado:
```
[provider] OpenAI-direct/gpt-4o-mini
[heartbeat_runner] DONE ... status=success
```

## 7. Expor via nginx/SSL da VPS

Adicionar ao nginx principal (ou Certbot):
```nginx
server {
    listen 443 ssl;
    server_name nexus.seu-dominio.com;
    
    location / {
        proxy_pass http://localhost:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
```

## Troubleshooting

### Container não sobe / unhealthy
```bash
docker logs evo-crm-community-evo-nexus-dashboard-1 --tail 50
```

### Levi não responde / falhas
```bash
# Ver últimas execuções
docker exec evo-crm-community-evo-nexus-dashboard-1 python3 -c "
import sqlite3
conn = sqlite3.connect('/workspace/dashboard/data/evonexus.db')
cur = conn.cursor()
cur.execute(\"SELECT heartbeat_id, status, started_at, substr(error,1,200) FROM heartbeat_runs ORDER BY started_at DESC LIMIT 10\")
for r in cur.fetchall(): print(r)
"
```

### Resetar containers (sem perder dados dos volumes)
```bash
docker compose -f docker-compose.nexus.yml up -d --force-recreate
```

### Backup dos volumes
```bash
docker run --rm -v nexus_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/nexus_data_$(date +%Y%m%d).tar.gz -C /data .
```

## Volumes e persistência

| Volume | Conteúdo | Persistir? |
|--------|----------|------------|
| `nexus_config` | heartbeats.yaml, providers.json, workspace settings | ✅ Sim |
| `nexus_data` | SQLite DB (runs, heartbeats, goals) | ✅ Sim |
| `nexus_claude` | Agents .md, settings, memory | ✅ Sim |
| `nexus_memory` | Memória compartilhada dos agentes | ✅ Sim |
| `nexus_logs` | Logs de execução | ⚠️ Rotacionar |
| `nexus_workspace` | Arquivos de trabalho dos agentes | ✅ Sim |
| `nexus_venv` | Virtualenv Python | ♻️ Regenerável |

Na primeira instalação na VPS os volumes serão criados vazios —
o boot script (`nexus-boot.py`) e os heartbeats irão popular tudo automaticamente.

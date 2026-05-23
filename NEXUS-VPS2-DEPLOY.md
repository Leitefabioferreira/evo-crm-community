# EvoNexus — Deploy VPS 2 (standalone)

## Arquitetura

```
VPS 1 (adam.servecounterstrike.com)        VPS 2 (nova VPS)
┌─────────────────────────────────┐        ┌──────────────────────────────┐
│  Evolution API  :443/api        │◄──────►│  nexus-dashboard  :8080      │
│  Evo CRM        :443/crm        │        │  nexus-scheduler             │
│  PostgreSQL     :5432           │        │  nginx (SSL)  :80/:443       │
│  Redis / RabbitMQ               │        └──────────────────────────────┘
└─────────────────────────────────┘           Tamanho: ~6GB de imagens
         ~7GB de imagens                      VPS 10-20GB é suficiente
```

## Pré-requisitos VPS 2

- Ubuntu 22.04+, mínimo **20GB disco**, 2GB RAM
- Docker + Docker Compose instalados
- Domínio apontando para o IP da VPS 2, ex: `nexus2.seudominio.com`
- Portas 80 e 443 abertas no firewall

## 1. Instalar Docker (se necessário)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

## 2. Copiar arquivos para a VPS 2

Da sua máquina local, copiar os 4 arquivos necessários:

```bash
# Substituir USER@VPS2_IP pelo endereço da nova VPS
scp nexus-boot.py \
    docker-compose.nexus-vps2.yml \
    nexus-nginx-vps2.conf \
    .env.nexus-vps2.example \
    USER@VPS2_IP:~/nexus/
```

## 3. Configurar na VPS 2

```bash
cd ~/nexus
cp .env.nexus-vps2.example .env.nexus
nano .env.nexus   # preencher EVONEXUS_SECRET_KEY e DASHBOARD_API_TOKEN
```

Gerar os secrets:
```bash
python3 -c "import secrets; print('EVONEXUS_SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import secrets; print('DASHBOARD_API_TOKEN=' + secrets.token_hex(32))"
```

## 4. Configurar o nginx com seu domínio

```bash
# Substituir NEXUS_DOMAIN pelo seu domínio real
sed -i 's/NEXUS_DOMAIN/nexus2.seudominio.com/g' nexus-nginx-vps2.conf
```

## 5. Primeira subida (sem SSL)

Editar `nexus-nginx-vps2.conf` temporariamente — comentar o bloco HTTPS e servir só na 80 para obter o certificado:

```bash
# Subir apenas nginx + dashboard na porta 80 primeiro
docker compose -f docker-compose.nexus-vps2.yml up -d nexus-dashboard nginx
```

## 6. Obter certificado SSL

```bash
docker compose -f docker-compose.nexus-vps2.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d nexus2.seudominio.com \
  --email seuemail@gmail.com \
  --agree-tos --no-eff-email
```

## 7. Habilitar SSL e subir tudo

Restaurar o bloco HTTPS no `nexus-nginx-vps2.conf`, depois:

```bash
docker compose -f docker-compose.nexus-vps2.yml up -d
```

## 8. Verificar

```bash
# Status dos containers
docker compose -f docker-compose.nexus-vps2.yml ps

# Logs do boot
docker logs nexus-dashboard 2>&1 | grep '\[boot\]'

# Verificar heartbeats
docker logs nexus-dashboard 2>&1 | grep '\[runner\]\|\[provider\]' | tail -10
```

## Variáveis importantes no .env.nexus

| Variável | Valor |
|---|---|
| `EVO_CRM_URL` | `https://adam-crm.servecounterstrike.com` |
| `EVOLUTION_API_URL` | `https://adam-api.servecounterstrike.com` |
| `EVO_CRM_API_TOKEN` | token do Chatwoot/CRM (já configurado) |
| `EVOLUTION_API_KEY` | `13843d97a33afda28fdd04919c07ce7237c50d5bc93f235979cfd84af6ac0fd0` |

## Troubleshooting

```bash
# Ver logs em tempo real
docker logs -f nexus-dashboard

# Reiniciar sem perder dados
docker compose -f docker-compose.nexus-vps2.yml restart nexus-dashboard

# Recriar containers (mantém volumes)
docker compose -f docker-compose.nexus-vps2.yml up -d --force-recreate
```

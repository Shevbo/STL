# Deploy — Shectory Trader (hoster: 83.69.248.175)

Frontend and backend run on the same hoster machine. Nginx serves static files and proxies API/WS to FastAPI on port 8000.

## Build frontend

```bash
cd frontend && npm install && npm run build
```

## First deploy

```bash
# Copy app to hoster
rsync -av --exclude=node_modules --exclude=.git . ubuntu@83.69.248.175:/home/ubuntu/apps/shectory-trader/

# On hoster:
sudo cp /home/ubuntu/apps/shectory-trader/deploy/nginx.conf /etc/nginx/sites-available/shectory-trader
sudo ln -sf /etc/nginx/sites-available/shectory-trader /etc/nginx/sites-enabled/shectory-trader
sudo nginx -t && sudo systemctl reload nginx

# Start backend (once M8 API is built):
cd /home/ubuntu/apps/shectory-trader
poetry run uvicorn trader.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

## Update deploy

```bash
cd frontend && npm run build
rsync -av frontend/dist/ ubuntu@83.69.248.175:/home/ubuntu/apps/shectory-trader/frontend/dist/
```

Veri miktarından bağımsız olarak, Windows'un UDP (QUIC) ile yaşadığı bu uyumsuzluğu aşmanın en garantili yolu HTTP2 (TCP) protokolüne geçmektir. TCP, Windows ağ yığınında çok daha stabil çalışır.

cloudflared tunnel --url http://localhost:8000 --protocol http2


## Socket.IO Redis A/B Testi

Distributed mode acik (Redis adapter):

```powershell
$env:SOCKETIO_DISTRIBUTED_MODE = "true"
docker compose -f docker-compose.dev.yml -f docker-compose.lb.yml up --scale api=1
```

Distributed mode kapali (in-memory):

```powershell
$env:SOCKETIO_DISTRIBUTED_MODE = "false"
docker compose -f docker-compose.dev.yml -f docker-compose.lb.yml up --scale api=1
```

Redis pub/sub akis gozlemi:

```powershell
docker exec -it redis redis-cli monitor
```

Test bitince ortam degiskenini temizle:

```powershell
Remove-Item Env:SOCKETIO_DISTRIBUTED_MODE -ErrorAction SilentlyContinue
```


python -m api.app.main

python -m admin_panel.app.main 

python -m agent.cli


## Sorun Giderme

Eğer sunucuyu başlatırken portun kullanımda olduğuna dair bir hata alırsanız, aşağıdaki PowerShell komutu ile portu temizleyebilirsiniz:

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess -Force
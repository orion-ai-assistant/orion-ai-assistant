import httpx, json, time

payload={
    "user_id":"tester",
    "chat_id":"loadtest-chat",
    "request_id":"r1",
    "messages":[
        {"role":"system","content":"You are helpful."},
        {"role":"user","content":"Hello there"}
    ]
}

print('POSTing to agent...')
with httpx.stream('POST','http://localhost:8001/chat/stream', json=payload, timeout=60.0) as r:
    print('STATUS', r.status_code)
    for raw in r.iter_lines():
        if not raw:
            continue
        try:
            print('ND_RAW:', raw)
            j = json.loads(raw)
            print('ND_JSON:', json.dumps(j, indent=2, ensure_ascii=False))
        except Exception as e:
            print('ERR parse:', e)
print('done')

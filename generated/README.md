# generated/

Esta pasta fica vazia no host. O `transpile` grava o `agent.py` no volume Docker `generated`,
que só os containers do serviço `agent` montam. Para vê-lo:

```bash
docker compose run --rm agent cat generated/agent.py
```

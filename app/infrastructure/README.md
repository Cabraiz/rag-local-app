# Infraestrutura local

`compose` separa ambiente normal, resiliência, observabilidade, laboratórios opt-in e QA. `images` reúne os Dockerfiles. `dependencies` guarda locks com hashes e `broker` contém a configuração não secreta de RabbitMQ.

Use `app/manage.py runtime ps` para consultar o ambiente e `runtime config --quiet` para validar os perfis ativos. O projeto continua sendo `rag-local-v2`; volumes e segredos permanecem nos mesmos locais. Os perfis QA não devem ser acrescentados à aplicação principal.

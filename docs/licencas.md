# Licenças, fontes e dados

O código deste repositório está sob a licença [MIT](../LICENSE).

## Dependências

Licenças conferidas nos metadados instalados nas imagens Docker (`importlib.metadata` para os
pacotes Python e `/usr/share/doc/<pacote>/copyright` para os do Debian). O `agent` leva o ADK, o
cliente da Gemini API, o MCP e o httpx; pillow, pytesseract, o Tesseract e as fontes ficam nas imagens
do `ocr` e de testes (`test`), e o pytest só na de testes.

| Pacote | Versão | Licença | Uso |
|---|---|---|---|
| google-adk | 2.10.0 | Apache-2.0 | Agentes (`SequentialAgent`, `LlmAgent`), ferramentas MCP e OpenAPI |
| google-genai | 2.27.0 | Apache-2.0 | Cliente da Gemini API |
| mcp | 2.2.0 | MIT | Servidores e cliente MCP via SSE |
| fastapi | 0.141.1 | MIT | API de agendamento e Swagger |
| uvicorn | 0.54.0 | BSD-3-Clause | Servidor HTTP da API e dos MCP |
| pydantic | 2.13.5 | MIT | Validação da spec e dos contratos |
| cryptography | 50.0.2 | Apache-2.0 ou BSD-3-Clause | Cifra AES-GCM do banco |
| httpx | 0.28.1 | BSD-3-Clause | Cliente HTTP (CLI e testes) |
| pillow | 12.1.0 | MIT-CMU | Leitura de imagens para o OCR |
| pytesseract | 0.3.13 | Apache-2.0 | Ponte Python para o Tesseract |
| pytest | 8.4.2 | MIT | Testes |
| tesseract-ocr, libtesseract5 (Debian) | 5.5.0 | Apache-2.0 | Motor de OCR |
| tesseract-ocr-por, -eng, -osd (Debian) | 4.1.0 | Apache-2.0 | Dados de idioma do OCR |
| libleptonica6 (Debian) | 1.84.1 | BSD-2-Clause | Processamento de imagem do Tesseract |
| certifi (transitiva, via httpx e requests) | 2026.7.22 | MPL-2.0 | Certificados raiz para o HTTPS da Gemini API |
| fonts-dejavu-core, -mono (Debian, via fontconfig do Tesseract) | 2.37 | Bitstream Vera (mudanças do DejaVu em domínio público) | Fontes dos pedidos da carga (`tests/load/pedidos.py`) |
| python:3.12-slim (imagem base) | 3.12 | PSF-2.0 e licenças do Debian 13 | Base de todas as imagens |

As dependências diretas são permissivas (MIT, BSD, Apache-2.0, PSF): pedem que os avisos de
copyright sejam mantidos, e a Apache-2.0 também pede o arquivo NOTICE quando houver. Há copyleft em
duas partes:
- a **certifi**, dependência transitiva, é MPL-2.0, um copyleft fraco por arquivo: só quem
  modifica e distribui os arquivos dela precisa publicar essas mudanças. O projeto a usa sem
  modificação;
- a imagem base traz pacotes do Debian sob GPL/LGPL (por exemplo `bash` e `libc6`), usados
  como programas do sistema, sem modificação.

Nada disso obriga a mudar a licença deste código, que só importa e executa essas peças. As imagens
Docker não são distribuídas por este repositório: cada pessoa as constrói localmente.

## Fontes e imagens dos manuscritos

Os pedidos de `samples/manuscritos/` (e as cópias `samples/pedido-manuscrito*.png`) são
**renderizados** por `exemplos/gerar_manuscrito.py` com as fontes de letra de mão que vêm com o
Windows (Ink Free, Segoe Print e Segoe Script, além de Arial no cabeçalho e no carimbo). Nenhuma
fonte está no repositório nem é redistribuída: só as imagens resultantes estão versionadas. O
gerador procura as fontes na pasta de `FONTS_DIR` ou, sem ela, nas pastas de fontes do sistema
(Windows, Linux ou macOS). Para gerar as mesmas imagens é preciso ter essas fontes: fora do
Windows, copie-as para uma pasta e aponte `FONTS_DIR` para ela. Uma fonte que falta para o
gerador com uma mensagem que diz qual é e onde procurou. Todos os
nomes, CPFs (com dígito verificador errado de propósito), CRMs, datas e clínicas são fictícios.

Os pedidos digitados do teste de carga (`tests/load/pedidos.py`) são outra coisa: são gerados em
memória, dentro do container, com as fontes DejaVu da imagem (`/usr/share/fonts/truetype/dejavu`).
Fora da imagem, o gerador usa a fonte padrão do Pillow. Essas imagens ficam num volume tmpfs da
carga e não são gravadas no disco nem no repositório.

## Dados e IA

Ao Gemini vai só texto: as linhas do pedido já mascaradas pelo servidor de OCR (`[NOME]`,
`[CPF]`…), os códigos e nomes de exames do catálogo e um apelido da imagem
(`pedido-1.png`), nunca o nome real do arquivo. A imagem e os
dados pessoais nunca saem dos containers. O projeto funciona com a chave do plano gratuito, mas
as execuções deste repositório usaram a Gemini API paga. Pelos
[termos da Gemini API](https://ai.google.dev/gemini-api/terms) (seção "How Google Uses Your
Data"), nos serviços pagos o Google não usa os prompts nem as respostas para melhorar seus
produtos; nos gratuitos, pode usar. Para dados reais, use a API paga. O projeto não tem rastreamento nem telemetria: não há biblioteca de
analytics nem exportador OpenTelemetry configurado, e a única chamada para fora é a do serviço
`agent` à Gemini API (OCR e RAG ficam numa rede sem internet).

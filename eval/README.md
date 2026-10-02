# Qualidade e comprovantes

Esta pasta separa a evidência histórica dos scripts que executam testes. Scripts ficam em [app/tests](../app/tests); aqui ficam dados e resultados.

- `datasets`: conjuntos de avaliação controlados.
- `runs`: comprovantes, falhas e resultados originais. Não são reescritos pela reorganização.
- `logs`: saídas antigas agrupadas por área e etapa.
- `history`: resultados antigos que antes estavam soltos nas raízes.
- `resilience-runtime`: materiais da operação de resiliência.

Um comprovante válido identifica o escopo, a imagem, as fontes e os checks realmente executados. Duas rodadas locais não significam auditoria independente ou ausência de todo bug. O [registro da organização](../docs/organization/README.md) contém os critérios e o mapa de movimentos; sua validação terá um comprovante próprio.

"""Conservative lab answerability slots, not a general entailment/LLM judge."""
import re
import unicodedata

def tokens(text):
    value=''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]+',value))

def eligible(question,text):
    q=tokens(question); evidence=tokens(text)
    if q & {'senha','token','apikey','segredo','secret'}: return False
    bank_terms={'banco','bancaria','bancario','agencia','pix'}
    # A restaurant bill is not a bank account. Explicit banking intent always
    # wins; an unqualified "conta" remains conservative.
    meal_bill=bool(q & {'alimentacao','refeicao','refeicoes','restaurante'}
                   and q & {'coletiva','ratear','rateio','restaurante'})
    banking=bool(q & bank_terms or ('conta' in q and not meal_bill))
    if banking and (not evidence & (bank_terms | {'conta'})
                    or not re.search(r'\d{3,}',text)): return False
    groups=[
        {'hotel','hospedagem','diaria','diarias'}, {'taxi','uber','transporte','onibus'},
        {'vpn'}, {'receita','preparar','ingredientes','cozinhar'},
        {'industrial','fabrica'}, {'impressora'}, {'servidor'}, {'ferias'}]
    if any(q & group and not evidence & group for group in groups): return False
    if q & {'aprovar','aprova','aprovacao','autoriza','autorizar'} and not any(v.startswith(('aprova','autoriza')) for v in evidence): return False
    money=bool(q & {'valor','preco','orcamento','custo','custos','gastos','gastar','teto'}) or ('quanto' in q and not q & {'tempo','dias','horas'})
    # Procedures and qualitative effects on a cap need policy evidence, not
    # necessarily an amount. Explicit requests for amounts still require one.
    process=bool(q & {'como','quem'} or q & {'aumenta','aumentar','reduz','reduzir','consome','consumir'})
    if process and not q & {'quanto','valor','preco'}: money=False
    # Match against original ordered text, not a set of tokens.
    plain=''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))
    if money and not re.search(r'(?:r\$\s*\d|\d+(?:[.,]\d+)?\s*(?:reais|real|dolares))',plain): return False
    # "Quando trabalho em casa" is a condition, not a request for a date.
    temporal=bool(q & {'horario','horarios','horas','prazo','tempo'}) or question.strip().lower().startswith('quando ')
    if temporal and not evidence & {'dia','dias','hora','horas','uteis','semana','segunda','sexta'}: return False
    return True

def retrieval_query(question):
    """Controlled workplace device aliases, not an LLM rewrite or new fact.

    Only repair intent is expanded. Industrial/printer/server intent must still
    pass its own evidence slot; a machine in general is not assumed a computer.
    The original question remains unchanged in the canonical request ledger.
    """
    q=tokens(question)
    if not q & {'reparo','conserto','consertar','manutencao'} or q & {'industrial','fabrica','impressora','servidor'}: return question
    plain=''.join(c for c in unicodedata.normalize('NFKD',question.lower()) if not unicodedata.combining(c))
    aliases={'reparo':'manutencao','conserto':'manutencao','maquina':'computador','notebook':'computador'}
    return re.sub(r'\b(?:reparo|conserto|maquina|notebook)\b',lambda match:aliases[match.group()],plain)

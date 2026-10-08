"""What a run did, written in code from the session state, never from the model's own words: what
the OCR masked, the exams left out and why, and the appointment as the API returned it. `cli run`
prints these lines; under `adk run` or `adk web` the pipeline's last message is this report
(BookingCallbacks.report)."""
import re

from google.genai import errors

# Why an exam is asked although it is written clearly: what its line says (runtime/confianca.py, 'why').
WHY = {'uncertain': '; o pedido tem outras palavras além do exame', 'table': '; o pedido está em tabela ou colunas',
       'instruction': '; o pedido tem uma instrução sobre este exame', 'page': '; o pedido tem texto além da lista de exames',
       'form': '; formulário com marcas: só os marcados contam; confira', 'longer': '; o nome escrito é de outro exame, mais longo'}
# How each exam left out is shown, by its reason; {guess} is "'<line read>' → <exam> <code> (confiança 0,xx)".
LEFT_OUT = {
    'needs_confirmation': 'não agendado sem confirmação: {guess}; rode num terminal, sem --yes, para responder',
    'declined': 'não incluído (você respondeu não): {guess}',
    # a yes to a call that resumed after another call of the same turn had already booked the run's appointment
    'after_booking': ('não agendado (você confirmou, mas o agendamento desta execução já tinha sido criado): {guess}; '
                      'agende-o à parte'),
    'omitted': 'não incluído pelo agente: {guess}; confira o pedido',  # a search found it, the model left it out
    'not_searched': 'não buscado pelo agente: {guess}; confira o pedido',  # only the check of the whole order found it
    'score': 'baixa confiança: {guess}; confira o pedido',
}
# Left out for what the order says, whatever the confidence; {seen} is "'<line read>' → <exam> <code>".
REFUSED = {
    'negated': 'não agendado: {seen}; o pedido diz para não realizar',
    'history': 'não agendado: {seen}; o pedido diz que já foi realizado',
    'prep': 'não agendado: {seen}; a linha é uma orientação de preparo, não um pedido',
    'line_used': 'não agendado: {seen}; o mesmo trecho da linha já foi usado por {used_by}; confira o pedido',
}
UNDECIDED = ('omitted', 'not_searched')  # exams of the order the agent made no decision on


def confidence(value):
    return f'{value:.2f}'.replace('.', ',')


def printable(text):
    """OCR text, shown to a person: no terminal codes."""
    return ''.join(char for char in str(text) if char.isprintable())


def left_out_line(item):
    """One exam left out, with the same number and words as the [s/N] question (the confidence the policy
    decided on), or with what the order says of it."""
    seen = f"'{printable(item['read'])}' → {item['name']} {item['code']}"
    if item.get('reason') in REFUSED:
        return REFUSED[item['reason']].format(seen=seen, used_by=item.get('used_by'))
    guess = f"{seen} (confiança {confidence(item['confidence'])})"
    if item.get('why') in WHY:  # asked, not booked, for what its line says
        guess += f'{WHY[item["why"]]}, confirme'
    return LEFT_OUT.get(item.get('reason'), LEFT_OUT['score']).format(guess=guess)


def unrecognized(values):
    """Numbers of the list items the OCR read but could not tell as exams: the CLI's record has them, the
    session's record has the OCR's line_intent."""
    if 'unrecognized' in values:
        return values['unrecognized']
    return [index + 1 for index, kind in enumerate(values.get('ocr_intent') or []) if kind == 'unrecognized']


def reading_lines(values):
    """What the OCR masked or removed, the exams the person confirmed and the ones left out. `values`:
    the session state, or the CLI's record of the run (the same keys)."""
    masked = ', '.join(f'{kind} x{count}' for kind, count in (values.get('pii_masked') or {}).items())
    lines = [f'PII mascarada pelo OCR: {masked or "nenhuma"}']
    if values.get('text_removed'):  # not PII by the rules, but it may hold a name they did not recognize
        lines.append(f'Trechos removidos pelo OCR (não pareciam exame): {values["text_removed"]}')
    if values.get('instructions_removed'):
        lines.append(f'Instruções neutralizadas no OCR: {values["instructions_removed"]}')
    if values.get('cancel_unlinked'):  # guardrails/intent.py: nothing on the page books alone
        lines.append('Aviso: o pedido tem um cancelamento que não foi ligado a um exame; confira')
    # the text of an unrecognized line never leaves the OCR: only where it is
    lines += [f'lido mas não reconhecido no catálogo: linha {number}; confira o pedido' for number in unrecognized(values)]
    lines += [f"incluído com a sua confirmação: '{printable(item['read'])}' → {item['name']} {item['code']}"
              for item in values.get('confirmed') or []]
    lines += [left_out_line(item) for item in values.get('low_confidence') or []]
    if values.get('order_unchecked'):
        lines.append('Aviso: o pedido não foi conferido por inteiro (a busca no catálogo não respondeu); confira o pedido')
    return lines


def api_refusal(error):
    # ADK's RestApiTool reports a non-2xx reply as
    # {"error": "Tool ... execution failed ... Status Code: <n>, <response body>"}.
    refused = re.search(r'Status Code: (\d+), (.*)', str(error), re.S)
    return f'HTTP {refused[1]}: {refused[2].strip()}' if refused else str(error)


def api_error_in(error):
    """The Gemini API error inside an agent failure, if any (ADK may wrap it)."""
    while error is not None and not isinstance(error, errors.APIError):
        error = error.__cause__ or error.__context__
    return error


def model_failure(code, message):
    """A model call Gemini refused, also with the reserve model (runtime/adk.py): one line."""
    if code in (429, 500, 503):
        return f'Gemini indisponível no momento (HTTP {code}); tente novamente'
    return f'o Gemini recusou a chamada (HTTP {code}: {str(message)[:500]})'


def not_booked(state):
    """Why a run that should book booked nothing, from what the callbacks kept."""
    if state.get('model_error'):
        return state['model_error']
    if state.get('file_refused'):
        return 'o agente pediu um arquivo diferente do informado'
    if state.get('ocr_error'):
        return f'OCR recusou a imagem: {state["ocr_error"]}'
    if 'ocr_lines' not in state:
        return 'o pedido não foi lido (o agente não chamou o OCR, ou o OCR não respondeu)'
    if state.get('blocked'):
        return f'agendamento bloqueado antes de chamar a API: {state["blocked"]}'
    if state.get('api_error'):
        return f'a API recusou o agendamento ({api_refusal(state["api_error"])})'
    if not state.get('candidates'):
        return 'Nenhum exame encontrado no pedido'
    return 'o agente terminou sem um agendamento confirmado pela API'


def appointment_lines(appointment, left_out):
    """The exams the API stored and the appointment line, or None if the reply is not one."""
    try:
        exams = [f'- {exam["name"]} ({exam["code"]})' for exam in appointment['exams']]
        line = f'Agendamento confirmado pela API: id {appointment["id"]}, status {appointment["status"]}'
    except (KeyError, TypeError):
        return None
    undecided = sum(item.get('reason') in UNDECIDED for item in left_out)
    if undecided:
        line += (f'; ATENÇÃO: {undecided} possível(is) exame(s) do pedido sem decisão do agente, '
                 'confira os avisos acima')
    return [*exams, line]


def listing_lines(state):
    listing = state.get('listing') or []
    lines = [f'ignorado: {code} não veio de nenhuma busca no catálogo' for code in state.get('invented') or []]
    lines += [f'- {item["name"]} ({item["code"]}), confiança {confidence(item["confidence"])}'
              + (' (confira)' if item['check'] else '') for item in listing]
    lines.append(f'{len(listing)} exame(s) listado(s); nada foi agendado' if listing
                 else 'Nenhum exame listado com confiança suficiente; nada foi agendado')
    return lines


def report(state, books):
    """The run's final message. `books`: the spec has a booking role (else it lists exams)."""
    values = dict(state) | {'low_confidence': [*(state.get('low_confidence') or []), *(state.get('unreported') or [])]}
    lines = reading_lines(values)
    if not books:
        return '\n'.join(lines + (listing_lines(state) if 'ocr_lines' in state else
                                  [f'{not_booked(state)}; nada foi listado']))
    appointment = state.get('booked_appointment')
    booked = appointment_lines(appointment, values['low_confidence']) if isinstance(appointment, dict) else None
    if booked is None and isinstance(appointment, dict):
        booked = ['a API respondeu sem o formato esperado (id, status e exams com code e name); confira o agendamento']
    return '\n'.join(lines + (booked or [f'{not_booked(state)}; nada foi agendado']))

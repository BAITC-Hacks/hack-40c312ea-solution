"""Deterministic response boundaries; model text is never a source of product facts."""
import re

from .integration import CONTACTS


class CatalogUnavailable(Exception):
    """Current product facts could not be verified."""


def reply(action, ru, kk, lang, *, manager=False):
    return {'action': action, 'message': kk if lang == 'kk' else ru,
            'products': [], 'events': [], 'warnings': [], 'route': 'DIRECT',
            'handoff_available': manager}


def input_guard(text, lang):
    value = text.casefold()
    if re.search(r'игнорир\w*.*(?:инструкц|правил)|ignore.*(?:instructions|rules)|system prompt|системн\w*\s+(?:промпт|инструкц)|придум\w*.*(?:цен|остат|сертифик)|выдум\w*|без подтвержден|bypass|ережелерді елеме', value):
        return reply('boundary', 'Цены, остатки и характеристики беру только из каталога. Действия с корзиной требуют отдельного подтверждения. Укажите товар или задачу подбора.',
                     'Баға, қор және сипаттамалар тек каталогтан алынады. Себет әрекеттері бөлек растауды қажет етеді. Тауарды немесе таңдау тапсырмасын көрсетіңіз.', lang)
    if re.search(r'api[ _-]?key|api[ -]?ключ|парол|секрет|данные клиентов|құпия|cvv|номер карты|\b(?:\d[ -]?){13,19}\b', value):
        return reply('privacy_guard', 'Не отправляйте платёжные данные. Я не принимаю оплату и не раскрываю ключи, пароли или чужие данные. Могу помочь с каталогом EKT.',
                     'Төлем деректерін жібермеңіз. Төлем қабылдамаймын, кілттерді, құпиясөздерді немесе басқа клиенттердің деректерін ашпаймын. EKT каталогымен көмектесе аламын.', lang)
    if re.search(r'бомб|взрывчат|убить|поражени\w* током|обойти\w*\s+(?:защит|счетчик)|отключить\s+(?:узо|защиту)|в обход\s+(?:узо|защиты)', value):
        return reply('unsafe_request', 'Не могу помочь с опасным применением оборудования или обходом защиты. Для безопасного подбора обратитесь к квалифицированному специалисту или менеджеру EKT.',
                     'Жабдықты қауіпті қолдануға немесе қорғанысты айналып өтуге көмектесе алмаймын. Қауіпсіз таңдау үшін білікті маманға немесе EKT менеджеріне хабарласыңыз.', lang, manager=True)
    if re.search(r'погод|анекдот|стих|политик|президент|рецепт|диагноз|лечени|криптовалют|ставк\w* на спорт|weather|joke|өлең|ауа райы', value):
        return reply('out_of_scope', 'Я помогаю с электротехническим каталогом EKT, наличием, характеристиками и подбором. Задайте вопрос о товаре или спецификации.',
                     'EKT электротехникалық каталогы, қор, сипаттамалар және таңдау бойынша көмектесемін. Тауар немесе спецификация туралы сұрақ қойыңыз.', lang)
    if re.fullmatch(r'\s*(?:привет|здравствуй(?:те)?|сәлем|hello|спасибо|рахмет)[!. ]*', value):
        return reply('greeting', 'Здравствуйте! Укажите товар, артикул или задачу. Проверю каталог и отмечу, каких данных не хватает.',
                     'Сәлеметсіз бе! Тауарды, артикулды немесе тапсырманы көрсетіңіз. Каталогты тексеріп, жетіспейтін деректерді белгілеймін.', lang)
    return None


def offer_manager(result, lang):
    if result.get('handoff_available'):
        result['manager_contact'] = CONTACTS.copy()
        result['message'] = result.get('message', '') + (' Менеджерге «Менеджер» батырмасы арқылы хабарласыңыз.' if lang == 'kk' else ' Свяжитесь с менеджером через кнопку «Менеджер».')
    return result


def service_failure(lang):
    return reply('catalog_unavailable', 'Не удалось проверить актуальные данные каталога. Цену, остаток и совместимость сейчас подтвердить не могу. Попробуйте позже или свяжитесь с менеджером.',
                 'Каталогтың өзекті деректерін тексеру мүмкін болмады. Бағаны, қорды және үйлесімділікті қазір растай алмаймын. Кейінірек қайталаңыз немесе менеджерге хабарласыңыз.', lang, manager=True)


async def product_fact(text, state, engine, lang):
    """Answer a fact question using live detail only, never a model completion."""
    lower = text.casefold()
    fields = [('certificate', r'сертифик|сертиф'), ('warranty', r'гаранти|кепілдік'),
              ('voltage', r'напряжен|кернеу'), ('breaking_capacity', r'отключающ|ажырату қабілет'),
              ('current', r'номинальн\w* ток|номиналды ток'), ('stock', r'сколько осталось|остаток|қанша қалды|қорда бар'),
              ('price', r'сколько стоит|какая цена|бағасы қанша')]
    field = next((name for name, pattern in fields if re.search(pattern, lower)), None)
    if not field:
        return None
    pid = re.search(r'\b(?:id\s*[=:]?\s*)?(\d{5,7})\b', lower)
    selected = state.get('last_result', {}).get('selected') or {}
    pid = int(pid.group(1)) if pid else selected.get('id')
    if not pid:
        return reply('clarification', 'Укажите ID или сначала выберите товар — проверю конкретное поле в его карточке.',
                     'ID көрсетіңіз немесе алдымен тауарды таңдаңыз — оның карточкасындағы деректі тексеремін.', lang)
    result = await engine.solution(str(pid))
    card = next((p for p in result['products'] if p['id'] == pid), None)
    if not card:
        return reply('missing_fact', 'Товар с этим ID не найден. Проверьте ID или обратитесь к менеджеру.', 'Бұл ID бойынша тауар табылмады. ID тексеріңіз немесе менеджерге хабарласыңыз.', lang, manager=True)
    value = card.get(field) if field in {'certificate', 'price'} else card.get('quantity') if field == 'stock' else card['attributes'].get(field)
    labels = {'certificate': ('Сертификат', 'Сертификат'), 'warranty': ('Гарантия', 'Кепілдік'), 'voltage': ('Напряжение', 'Кернеу'), 'breaking_capacity': ('Отключающая способность', 'Ажырату қабілеті'), 'current': ('Номинальный ток', 'Номиналды ток'), 'stock': ('Остаток', 'Қор'), 'price': ('Цена', 'Баға')}
    label = labels[field][lang == 'kk']
    present = value is not None and value != ''
    message = f'{label}: {value}' if present else (f'{label}: дерек API-де жоқ; болжам жасамаймын.' if lang == 'kk' else f'{label}: данные в API отсутствуют; не буду их предполагать.')
    result.update(action='product_fact', message=message, handoff_available=not present or bool(card['warnings']))
    result['products'] = [card]
    result['selected'] = card
    state['last_result'] = result.copy()
    return result

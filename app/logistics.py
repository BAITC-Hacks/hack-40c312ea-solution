"""Explicit presentation fixtures, never used as real inventory or checkout stock."""
import os
import re

CITIES = ('Алматы', 'Астана', 'Шымкент', 'Караганда')


def settings(state):
    return {'enabled': state.get('demo_logistics', os.getenv('DEMO_LOGISTICS') == '1'),
            'city': state.get('delivery_city', 'Алматы'), 'source': 'simulated'}


def annotate(result, state, text, lang='ru'):
    for pattern, city in [(r'алмат', 'Алматы'), (r'астан', 'Астана'), (r'шымкент', 'Шымкент'), (r'караганд', 'Караганда')]:
        if re.search(pattern, text, re.I):
            state['delivery_city'] = city
    config = settings(state)
    if not config['enabled'] or not result.get('products'):
        return result
    city = config['city']
    for p in result['products']:
        locations = [{'city': name, 'quantity': (int(p['id']) * (i + 3)) % 40,
                      'days_min': 1 + (int(p['id']) + i) % 3,
                      'days_max': 2 + (int(p['id']) + i) % 3} for i, name in enumerate(CITIES)]
        destination = next(x for x in locations if x['city'] == city)
        p['demo_logistics'] = {'source': 'simulated', 'label': 'ДЕМО / DEMO',
                               'destination': destination, 'locations': locations}
    speed = bool(re.search(r'быстр|скор|жылдам|тез', text, re.I))
    if speed:
        result['products'].sort(key=lambda p: (bool(p.get('warnings')), not bool(p.get('quantity')),
            p['demo_logistics']['destination']['days_max'], p.get('price') is None, p.get('price') or 0))
        result['selected'] = next((p for p in result['products'] if p.get('quantity') and not p.get('warnings')), None)
        result['total'] = result['selected']['price'] if result['selected'] else None
    result['logistics'] = config
    result['message'] += (f' ДЕМО доставки: {city}. Сроки и остатки по городам ниже — виртуальный сценарий, не обещание EKT. '
                          + ('Сначала более быстрый демосрок, затем меньшая цена. ' if speed else '')
                          + 'Корзина проверяется только по реальному остатку EKT.' if lang == 'ru' else
                          f' DEMO жеткізу: {city}. Мерзімдер мен қалалардағы қор — виртуалды деректер. Себет EKT нақты қорымен тексеріледі.')
    return result

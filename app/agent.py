import asyncio
import re


async def handle_query(text: str, mode: str, engine, router) -> dict:
    if any(str(p.get('article', '')).casefold() == text.strip().casefold() for p in engine.catalog):
        return await engine.solution(text, mode)
    level = router.level(text)
    if level != 'STRONG':
        phrase, route = await router.normalize(text)
        result = await engine.solution(phrase, mode)
        result['query'] = text
        result['route'] = route
        return result

    plan, route = await router.plan(text)
    if not plan:
        result = await engine.solution(text, mode)
        result['route'] = route
        result['warnings'].append('Для точного проектирования нужны паспортные параметры и проверка инженера.')
        return result

    semaphore = asyncio.Semaphore(2)

    async def search_line(requirement):
        async with semaphore:
            return await engine.solution(requirement['description'], mode)

    found = await asyncio.gather(*(search_line(req) for req in plan['requirements']), return_exceptions=True)
    needs_motor_current = 'двигател' in text.casefold() and not re.search(r'\b\d{1,4}\s*[aа](?!\w)', text, re.I)
    items = []
    cards = []
    total = 0
    complete_total = True
    for requirement, result in zip(plan['requirements'], found):
        products = result.get('products', []) if isinstance(result, dict) else []
        # A generated component role is a proposal, never an approved engineering BOM.
        # Require an explicit specification before enabling bulk cart addition.
        chosen = None
        if needs_motor_current:
            chosen = None
        status = 'candidate' if chosen else ('clarification' if products else 'unavailable')
        display = chosen or (products[0] if products else None)
        if display:
            item = dict(display)
            item['role'] = requirement['role']
            item['reason'] = f"{requirement['role']}: " + item['reason']
            if needs_motor_current:
                item['warnings'] = item['warnings'] + ['Предварительный кандидат: укажите номинальный ток двигателя перед подбором комплекта.']
            cards.append(item)
        if chosen:
            if chosen['price'] is None:
                complete_total = False
            else:
                total += chosen['price'] * requirement['quantity']
        else:
            complete_total = False
        items.append({'requirement': requirement, 'status': status, 'chosen': chosen, 'alternatives': products[:3]})
    warnings = plan['warnings'] + plan['questions']
    if needs_motor_current:
        warnings.append('Нужен номинальный ток двигателя с шильдика; подбор комплекта пока предварительный.')
    warnings.append('Совместимость комплекта не подтверждена без паспортных параметров и проверки инженера.')
    return {
        'query': text, 'mode': mode, 'products': cards,
        'solution_items': items, 'selected': next((item['chosen'] for item in items if item['chosen']), None),
        'total': total if complete_total else None,
        'route': route,
        'events': ['Requirements extracted', 'Catalog searched', f'{len(cards)} components checked live', 'Compatibility uncertain', 'Solution generated'],
        'warnings': warnings,
        'clarification_questions': plan['questions'],
    }

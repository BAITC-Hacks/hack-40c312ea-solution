"""Explicit, fictional offline data; never presented as live EKT stock."""
import copy

PRODUCTS = []
for pid, name, price, props in [
    (900001, 'Щит распределительный 24 модуля DEMO', 18500, {}),
    (900002, 'DIN рейка 35 мм DEMO', 650, {}),
    (900003, 'Шина N/PE DEMO', 1200, {}),
    (900004, 'Кабельный ввод M20 DEMO', 280, {}),
    (900005, 'Автомат 1P 16А DEMO', 2400, {'NOMINALNYY_TOK': '16 А', 'KOLICHESTVO_POLYUSOV': '1'}),
    (900006, 'Кабель ВВГнг 3x2.5 DEMO', 850, {}),
]:
    PRODUCTS.append({'id': pid, 'name': name, 'article': 'DEMO-' + str(pid), 'price': price, 'quantity': 20,
                     'description': 'Вымышленные данные для демонстрации. Не являются предложением магазина.',
                     'stores': [{'id': 13, 'name': 'Алматы DEMO', 'quantity': 20}],
                     'properties': {'KRATNOST_MIN': '1', **props}, 'url': '', 'image': ''})


class DemoClient:
    async def detail(self, pid):
        return copy.deepcopy(next((p for p in PRODUCTS if p['id'] == pid), {}))

    async def close(self):
        pass


async def enable_demo(engine):
    await engine.ekt.close()
    engine.catalog = copy.deepcopy(PRODUCTS)
    engine.ekt = DemoClient()

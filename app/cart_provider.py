"""Cart adapter boundary. A production adapter must use the authenticated EKT session."""
import copy
from typing import Protocol


class CartProvider(Protocol):
    def add(self, state: dict, items: list[dict]) -> None: ...
    def replace(self, state: dict, index: int, items: list[dict]) -> None: ...
    def receipt(self, state: dict) -> dict: ...


class LocalCartProvider:
    def add(self, state, items):
        state['cart'].extend(copy.deepcopy(items))

    def replace(self, state, index, items):
        state['cart'][index:index + 1] = copy.deepcopy(items)

    def receipt(self, state):
        return {'cart': copy.deepcopy(state['cart']), 'cart_type': 'local_prototype',
                'cart_url': '/cart', 'official_cart_url': 'https://ekt.kz/personal/cart/'}


cart_provider: CartProvider = LocalCartProvider()

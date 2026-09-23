function askCartConfirmation(message) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog';
    const heading = document.createElement('h3');
    heading.textContent = 'Подтвердите добавление';
    const body = document.createElement('p');
    body.textContent = message + ' Официальная корзина EKT при этом не изменится.';
    const actions = document.createElement('div');
    actions.className = 'confirm-actions';
    const cancel = document.createElement('button');
    cancel.textContent = 'Отмена';
    cancel.onclick = () => {overlay.remove(); resolve(false);};
    const accept = document.createElement('button');
    accept.className = 'confirm';
    accept.textContent = 'Да, добавить';
    accept.onclick = () => {overlay.remove(); resolve(true);};
    actions.append(cancel, accept);
    dialog.append(heading, body, actions);
    overlay.append(dialog);
    document.body.append(overlay);
  });
}

add = async function(product) {
  try {
    const prepared = await api('/api/cart/prepare', {product_id: product.id, quantity: 1});
    if (!await askCartConfirmation(prepared.message)) return;
    const confirmed = await api('/api/cart/confirm', {
      product_id: product.id,
      quantity: 1,
      confirmation_token: prepared.confirmation_token
    });
    const target = document.getElementById('cart');
    target.replaceChildren();
    for (const item of confirmed.cart) {
      const line = document.createElement('div');
      line.textContent = `${item.quantity} × ${item.name} — ${money(item.price)}`;
      target.append(line);
    }
    const link = document.createElement('a');
    link.href = confirmed.official_cart_url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Открыть официальную корзину EKT ↗';
    target.append(link);
    bubble('Позиция добавлена в локальный прототип корзины. Для оформления добавьте её в официальную корзину EKT по ссылке.');
  } catch (error) {
    bubble('Не удалось добавить: ' + error.message);
  }
};


add = async function(product) {
  try {
    const prepared = await api('/api/cart/prepare', {product_id: product.id, quantity: 1});
    if (!confirm(prepared.message + '\nОфициальная корзина EKT при этом не изменится.')) return;
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

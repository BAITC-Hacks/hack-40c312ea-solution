document.getElementById('file').onchange = async () => {
  const file = document.getElementById('file').files[0];
  if (!file) return;
  bubble('Загружаю спецификацию: ' + file.name, 'user');
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch('/api/upload', {method: 'POST', body: form});
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || 'Ошибка чтения файла');
    bubble(`Распознано ${data.requirements} строк; обработано ${data.processed}.`);
    const target = document.getElementById('results');
    target.replaceChildren();
    for (const row of data.results) {
      const card = document.createElement('div');
      card.className = 'card';
      const best = row.chosen || row.products[0];
      card.textContent = `${row.status.toUpperCase()} · ${row.requirement.quantity} × ${row.requirement.description} → ${best ? best.name : 'Кандидат не найден'}`;
      target.append(card);
    }
    const summary = document.createElement('div');
    summary.className = 'activity';
    summary.textContent = data.total == null ? 'Полная сумма недоступна: часть позиций требует проверки.' : `Итого по выбранным позициям: ${money(data.total)}`;
    target.append(summary);
    const selected = data.results.filter(row => row.chosen).map(row => ({product_id: row.chosen.id, quantity: row.requirement.quantity}));
    if (selected.length) {
      const button = document.createElement('button');
      button.className = 'confirm';
      button.textContent = `Подготовить ${selected.length} позиции к добавлению`;
      button.onclick = async () => {
        try {
          const prepared = await api('/api/cart/prepare-batch', {items: selected});
          if (!confirm(prepared.message + '\nОфициальная корзина EKT не изменится.')) return;
          const confirmed = await api('/api/cart/confirm-batch', {confirmation_token: prepared.confirmation_token});
          document.getElementById('cart').textContent = confirmed.cart.map(item => `${item.quantity} × ${item.name}`).join(' · ');
          const link = document.createElement('a');
          link.href = confirmed.official_cart_url;
          link.textContent = 'Открыть официальную корзину EKT ↗';
          link.target = '_blank';
          document.getElementById('cart').append(link);
          bubble('Подтверждённые позиции добавлены в локальную корзину. На сайте EKT их потребуется добавить отдельно.');
        } catch (error) {
          bubble('Не удалось добавить комплект: ' + error.message);
        }
      };
      target.append(button);
    }
  } catch (error) {
    bubble('Файл не обработан: ' + error.message);
  }
};

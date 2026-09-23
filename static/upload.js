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
      const best = row.products[0];
      card.textContent = `${row.requirement.quantity} × ${row.requirement.description} → ${best ? best.name : 'Кандидат не найден'}`;
      target.append(card);
    }
  } catch (error) {
    bubble('Файл не обработан: ' + error.message);
  }
};

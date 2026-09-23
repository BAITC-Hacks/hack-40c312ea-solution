const originalSubmit = document.getElementById('query-form').onsubmit;
document.getElementById('query-form').onsubmit = async event => {
  const text = document.getElementById('query').value.trim();
  if (!/(оплат|достав|услови|самовывоз|минимальн.*заказ)/i.test(text)) {
    return originalSubmit(event);
  }
  event.preventDefault();
  bubble(text, 'user');
  try {
    const response = await fetch('/api/conditions');
    const conditions = await response.json();
    bubble(conditions.payment + ' ' + conditions.delivery + ' Минимальная партия не подтверждена. Источник: ' + conditions.source);
  } catch (error) {
    bubble('Не удалось получить подтверждённые условия покупки.');
  }
};



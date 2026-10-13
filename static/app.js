async function copyText(button,text){const status=button.parentElement.querySelector('.copy-status')||button.closest('.card,.message')?.querySelector('.copy-status');try{await navigator.clipboard.writeText(text);if(status)status.textContent='Скопировано';else button.textContent='Скопировано';}catch{if(status)status.textContent='Выделите текст и скопируйте вручную';}}document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>copyText(b,document.getElementById(b.dataset.copy).value)));document.querySelectorAll('[data-copy-codes]').forEach(b=>b.addEventListener('click',()=>copyText(b,[...document.querySelectorAll('.code-grid code')].map(c=>c.textContent).join('\n'))));
const orderUpdates = document.querySelector('[data-order-updates]');
if (orderUpdates) {
  let submitting = false;
  document.querySelectorAll('form').forEach(form => form.addEventListener('submit', () => { submitting = true; }));
  setInterval(async () => {
    if (document.hidden || submitting) return;
    try {
      const response = await fetch(orderUpdates.dataset.orderUpdates, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) return;
      const data = await response.json();
      if (data.version !== orderUpdates.dataset.orderVersion) window.location.reload();
    } catch { /* The manual refresh link remains available when the network is unavailable. */ }
  }, 10000);
}

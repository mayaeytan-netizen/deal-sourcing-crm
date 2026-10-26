function toggleModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('open');
}

function closeModalOnBackdrop(event, id) {
  if (event.target.id === id) {
    toggleModal(id);
  }
}

function confirmSend(button) {
  const form = button.closest('form');
  const checkbox = form && form.querySelector('input[name="really_send"]');
  if (checkbox && checkbox.checked) {
    return confirm('This will send a real email right now. Continue?');
  }
  return true;
}

function selectVariant(touchId, index) {
  const dataEl = document.getElementById('variant-data-' + touchId);
  const pickerEl = document.getElementById('variants-' + touchId);
  if (!dataEl || !pickerEl) return;

  const variants = JSON.parse(dataEl.textContent);
  const variant = variants[index];
  if (!variant) return;

  const subjectEl = document.getElementById('subject-' + touchId);
  const bodyEl = document.getElementById('body-' + touchId);
  if (subjectEl) subjectEl.value = variant.subject;
  if (bodyEl) bodyEl.value = variant.body;

  pickerEl.querySelectorAll('.variant-pill').forEach((pill, i) => {
    pill.classList.toggle('active', i === index);
  });
}

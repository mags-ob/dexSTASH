  // The big price follows the printing picked for adding. The source is plain text unless it's your own link.
  const chipData = document.getElementById('cv-chips');
  const chips = chipData ? JSON.parse(chipData.textContent) : {};
  document.querySelectorAll('#cv-add [data-printings] input').forEach(i => i.addEventListener('change', () => {
    const out = document.getElementById('cv-price-line'), chip = chips[i.value];
    // The big number is the printing's range across its sources (or its one price); Every price follows it.
    const big = document.getElementById('cv-price');
    big.textContent = i.dataset.price ? (chip && chip.big) || i.dataset.price : 'No price yet';
    big.classList.toggle('none', !i.dataset.price);
    big.classList.toggle('is-range', big.textContent.includes('–'));
    out.textContent = chip ? chip.text : '';
    out.hidden = !(chip && i.dataset.price);
    const lists = document.querySelectorAll('.cv-sources tbody[data-variant]');
    if ([...lists].some(t => t.dataset.variant === i.value)) lists.forEach(t => { t.hidden = t.dataset.variant !== i.value; });
  }));

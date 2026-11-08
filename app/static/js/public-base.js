    function dsPic(img) {
      const rest = JSON.parse(img.dataset.pics || "[]");
      if (rest.length) { const [url] = rest.shift(); img.dataset.pics = JSON.stringify(rest); img.src = url; return; }
      img.hidden = true;
      if (img.nextElementSibling) img.nextElementSibling.hidden = false;
    }
  

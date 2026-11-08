  // A picture that doesn't load moves on to the next one for the art, then to "No picture".
  function artPic(img) {
    const rest = JSON.parse(img.dataset.pics || "[]");
    if (rest.length) { img.src = rest.shift()[0]; img.dataset.pics = JSON.stringify(rest); return; }
    img.onerror = null;
    img.replaceWith(document.getElementById("nopic").content.cloneNode(true));
  }

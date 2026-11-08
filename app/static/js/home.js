  // The $↻ spins while prices update; the page comes back with the new numbers when it's done.
  (function poll() { setTimeout(() => fetch("/prices/status").then(r => r.json()).then(s => s.running ? poll() : location.reload()).catch(poll), 3000); })();

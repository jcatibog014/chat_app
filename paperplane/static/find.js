// Search in this chat. Without this file it all still works: the form is a plain GET, and a
// result opens the chat at its message (#m-<id>, outlined by CSS :target).
(() => {
  // ---------- The chat, opened from a result: centre the message, mark the words ----------
  const wanted = /^#m-(\d+)$/.exec(location.hash);
  const target = wanted && document.getElementById(`m-${wanted[1]}`);
  if (target) {
    const words = document.querySelector("[data-find-words]");
    const needle = words ? words.textContent.trim().toLowerCase() : "";
    const text = target.querySelector("[data-text]");
    if (needle && text) {
      // Only text nodes are touched (never HTML), so nothing in a message can become markup.
      const walker = document.createTreeWalker(text, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach((node) => {
        let rest = node;
        let at = rest.nodeValue.toLowerCase().indexOf(needle);
        while (at >= 0) {
          const hit = rest.splitText(at);
          rest = hit.splitText(needle.length);
          const mark = document.createElement("mark");
          mark.className = "find-mark";
          hit.replaceWith(mark);
          mark.append(hit);
          at = rest.nodeValue.toLowerCase().indexOf(needle);
        }
      });
    }
    requestAnimationFrame(() => {
      target.scrollIntoView({ block: "center" });
      target.classList.add("is-flash");
      setTimeout(() => target.classList.remove("is-flash"), 2400);
    });
  }

  // ---------- The search page: results as you type ----------
  const form = document.querySelector("[data-find-form]");
  const results = document.querySelector("[data-find-results]");
  if (!form || !results) return;
  let timer = null;
  let asked = 0;
  const update = async () => {
    const params = new URLSearchParams(new FormData(form));
    for (const [key, value] of [...params]) if (!value) params.delete(key);
    const url = `${form.action}${params.toString() ? "?" + params : ""}`;
    const mine = ++asked;
    try {
      const response = await fetch(url, { headers: { Accept: "text/html" } });
      if (!response.ok || mine !== asked) return;  // an older answer arriving late is dropped
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const fresh = page.querySelector("[data-find-results]");
      if (fresh) {
        results.replaceChildren(...fresh.childNodes);
        history.replaceState(null, "", url);
      }
    } catch (error) {
      // Offline: the Search button still works when the connection is back.
    }
  };
  const soon = () => {
    clearTimeout(timer);
    timer = setTimeout(update, 250);
  };
  form.querySelector("input[name=q]").addEventListener("input", soon);
  form.querySelector("select[name=from]").addEventListener("change", update);
})();

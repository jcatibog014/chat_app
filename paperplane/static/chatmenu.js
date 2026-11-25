// The chat list's choices (mute, mark as read, restrict, block, leave, delete).
// Computers: the ⋯ menu beside each chat's time (plain forms; this file only fills "next").
// Phones: no ⋯ (CSS hides it); a long press on a chat opens a preview of the conversation with
// the same choices on a card under it (/chats/<id>/peek). Without JavaScript the ⋯ forms still
// work, and the chat's own header (bell, info) has the rest.
(() => {
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.content : "";

  // Every choice comes back to the page you're on (and a row drawn live for this tab gets this
  // reader's own CSRF token, never someone else's).
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.closest || !form.closest(".row-menu, [data-peek]")) return;
    const next = form.querySelector("[data-next]");
    if (next) next.value = location.pathname + location.search;
    if (!form.querySelector('input[name="csrf_token"]')) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = csrfToken;
      form.prepend(input);
    }
  }, true);

  // Only one ⋯ menu open at a time; opening one closes the others.
  document.addEventListener("toggle", (event) => {
    const menu = event.target;
    if (!menu.classList || !menu.classList.contains("row-menu") || !menu.open) return;
    document.querySelectorAll(".row-menu[open]").forEach((other) => other !== menu && (other.open = false));
  }, true);

  // ---------- Phones: long press for the preview ----------
  const phone = window.matchMedia("(max-width: 56rem)");
  const HOLD = 480;   // ms
  const SLACK = 10;   // px a finger may wander
  let timer = null;
  let start = null;
  let swallowClick = false;
  let layer = null;
  let opener = null;

  const cancel = () => {
    clearTimeout(timer);
    timer = null;
  };

  const close = () => {
    if (!layer) return;
    layer.remove();
    layer = null;
    document.body.classList.remove("has-peek");
    if (opener) opener.focus({ preventScroll: true });
  };

  const open = async (row) => {
    const id = row.dataset.chat;
    opener = row.querySelector("a");
    layer = document.createElement("div");
    layer.className = "peek-layer";
    layer.innerHTML = '<div class="peek-loading" aria-live="polite">Loading...</div>';
    document.body.append(layer);
    document.body.classList.add("has-peek");
    layer.addEventListener("click", (event) => {
      if (event.target === layer || event.target.closest("[data-peek-close]")) close();
    });
    try {
      const response = await fetch(`/chats/${id}/peek`, { headers: { Accept: "text/html" } });
      if (!response.ok) throw new Error(String(response.status));
      const html = await response.text();
      if (!layer) return;
      layer.innerHTML = html;
      const messages = layer.querySelector(".peek-messages");
      if (messages) messages.scrollTop = messages.scrollHeight;
      // Focus the preview itself (a screen reader reads it; Tab reaches the choices).
      const dialog = layer.querySelector("[data-peek]");
      if (dialog) dialog.focus({ preventScroll: true });
    } catch (error) {
      close();
      location.href = `/chats/${id}`;  // couldn't preview: just open it
    }
  };

  const list = document.querySelector("[data-chat-list]");
  if (list) {
    list.addEventListener("pointerdown", (event) => {
      const row = event.target.closest(".chat-row");
      if (!row || !phone.matches || event.button > 0 || event.target.closest(".row-menu")) return;
      start = { x: event.clientX, y: event.clientY };
      cancel();
      timer = setTimeout(() => {
        timer = null;
        swallowClick = true;
        if (navigator.vibrate) navigator.vibrate(15);
        open(row);
      }, HOLD);
    });
    list.addEventListener("pointermove", (event) => {
      if (timer && start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > SLACK) cancel();
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((type) => list.addEventListener(type, cancel));
    list.addEventListener("scroll", cancel, { passive: true });
    // The click that ends a long press doesn't open the chat.
    list.addEventListener("click", (event) => {
      if (!swallowClick) return;
      swallowClick = false;
      event.preventDefault();
      event.stopPropagation();
    }, true);
    // Phones show their own menu on a long press (copy link...): not on a chat row.
    list.addEventListener("contextmenu", (event) => {
      if (phone.matches && event.target.closest(".chat-row")) event.preventDefault();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && layer) close();
  });
})();

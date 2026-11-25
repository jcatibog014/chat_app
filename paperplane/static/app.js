// Extras only: every page works without this file. The live chat is socket.js and chat.js.

// The Appearance choice on the Settings page. theme.js applies it on every page.
function setUpTheme() {
  const section = document.querySelector("[data-theme-settings]");
  if (!section) return;
  const root = document.documentElement;
  section.querySelectorAll('input[name="theme"]').forEach((choice) => {
    choice.checked = choice.value === (root.dataset.theme || "system");
    choice.addEventListener("change", () => {
      try {
        if (choice.value === "system") {
          delete root.dataset.theme;
          localStorage.removeItem("paperplane-theme");
        } else {
          root.dataset.theme = choice.value;
          localStorage.setItem("paperplane-theme", choice.value);
        }
      } catch (error) {
        // Storage can be blocked. The theme still changes for this page.
      }
    });
  });
}

// Settings > Appearance: a colour shows at once and is saved to the account (the form's own
// Save button is for pages without JavaScript).
function setUpAccent() {
  const form = document.querySelector("[data-accent-form]");
  if (!form) return;
  form.querySelectorAll("[data-accent-choice]").forEach((choice) => {
    choice.addEventListener("change", () => {
      document.documentElement.dataset.accent = choice.value;
      form.requestSubmit();
    });
  });
}

// <details> menus (the profile menu, the Demo tag) close on an outside click or Escape.
function setUpPopovers() {
  const menus = () => document.querySelectorAll("details[data-popover][open]");
  document.addEventListener("click", (event) => {
    menus().forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    menus().forEach((menu) => {
      menu.open = false;
      menu.querySelector("summary").focus();
    });
  });
}

// "Are you sure?" in the app's own look (the browser's confirm box can't be styled).
// Answers true or false. Its main button says what the action does ("Leave group", "Delete message").
let confirmDialog = null;
function ask(question, action, style) {
  const danger = style === "button-danger";
  if (typeof HTMLDialogElement !== "function") return Promise.resolve(confirm(question));
  if (!confirmDialog) {
    confirmDialog = document.createElement("dialog");
    confirmDialog.className = "confirm-dialog";
    confirmDialog.innerHTML =
      '<form method="dialog"><p class="confirm-question" id="confirm-question"></p>' +
      '<div class="form-actions"><button class="button button-quiet" value="cancel">Cancel</button>' +
      '<button class="button" value="ok" data-confirm-ok></button></div></form>';
    confirmDialog.setAttribute("aria-labelledby", "confirm-question");
    // A click on the dimmed page around it is a Cancel.
    confirmDialog.addEventListener("click", (event) => {
      if (event.target === confirmDialog) confirmDialog.close("cancel");
    });
    document.body.append(confirmDialog);
  }
  const ok = confirmDialog.querySelector("[data-confirm-ok]");
  confirmDialog.querySelector(".confirm-question").textContent = question;
  ok.textContent = action || "OK";
  ok.className = style ? `button ${style}` : "button";
  confirmDialog.classList.toggle("is-danger", danger);
  confirmDialog.returnValue = "";
  confirmDialog.showModal();
  // Deleting: Cancel has the focus, so Enter is the safe choice. Otherwise the action has it.
  (danger ? confirmDialog.querySelector('[value="cancel"]') : ok).focus();
  return new Promise((resolve) => {
    confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "ok"), { once: true });
  });
}

// The words and colour of the button that was pressed, for the dialog's own button.
function actionOf(button) {
  return {
    action: button ? button.textContent.replace(/\s+/g, " ").trim() : "",
    style: button && button.classList.contains("button-danger") ? "button-danger" : "",
  };
}

function setUpConfirm() {
  const approved = new WeakSet();
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches || !form.matches("form[data-confirm]")) return;
    if (approved.has(form)) return approved.delete(form);
    event.preventDefault();
    const submitter = event.submitter || form.querySelector('[type="submit"]');
    const { action, style } = actionOf(submitter);
    if (await ask(form.dataset.confirm, action, style)) {
      approved.add(form);
      form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
    }
  });
}

// A select that saves itself (a role, a choice): no Save button needed.
function setUpAutoSubmit() {
  document.querySelectorAll("[data-auto-submit]").forEach((control) => {
    const button = control.form.querySelector("[data-auto-submit-button]");
    if (button) button.hidden = true;
    control.addEventListener("change", () => control.form.requestSubmit());
  });
}

// The Copy button beside a code or a link.
function setUpCopy() {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    const field = document.getElementById(button.dataset.copy);
    button.hidden = false;
    button.addEventListener("click", async () => {
      field.select();
      try {
        await navigator.clipboard.writeText(field.value);
      } catch (error) {
        document.execCommand("copy");
      }
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy"), 1500);
    });
  });
}

// The service worker (installable app) and the "Install app" button in Settings.
// Browsers that can install it fire "beforeinstallprompt"; only then does the button show.
function setUpInstall() {
  if ("serviceWorker" in navigator && document.querySelector('link[rel="manifest"]') && document.body.dataset.me) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Not a problem: the site works the same without it.
    });
  }
  const button = document.querySelector("[data-install]");
  if (!button) return;
  let offer = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // our own button instead of the browser's banner
    offer = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!offer) return;
    offer.prompt();
    await offer.userChoice;
    offer = null;
    button.hidden = true;
  });
  window.addEventListener("appinstalled", () => (button.hidden = true));
}

// Messages fade away after a few seconds (errors stay; data-toast-wait sets a longer time);
// the clock starts again while the mouse or keyboard is on a message. Every message can be closed.
function wireToast(toast) {
  const leave = () => {
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), 400);
  };
  const close = toast.querySelector("[data-toast-close]");
  close.hidden = false;
  close.addEventListener("click", leave);
  if (!toast.hasAttribute("data-toast-fade")) return;
  const wait = Number(toast.dataset.toastWait) || 6000;
  let timer = setTimeout(leave, wait);
  const hold = () => clearTimeout(timer);
  const resume = () => {
    if (toast.matches(":hover") || toast.contains(document.activeElement)) return;
    clearTimeout(timer);
    timer = setTimeout(leave, 4000);
  };
  toast.addEventListener("mouseenter", hold);
  toast.addEventListener("focusin", hold);
  toast.addEventListener("mouseleave", resume);
  toast.addEventListener("focusout", () => setTimeout(resume));
}

function setUpToasts() {
  document.querySelectorAll(".toast").forEach(wireToast);
}

// A toast made by a script (a send that failed, the connection back). error: stays until closed.
function toast(text, error) {
  let box = document.querySelector(".toasts");
  if (!box) {
    box = document.createElement("div");
    box.className = "toasts";
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    document.body.append(box);
  }
  const note = document.createElement("p");
  note.className = `toast ${error ? "toast-error" : "toast-ok"}`;
  if (!error) note.setAttribute("data-toast-fade", "");
  const words = document.createElement("span");
  words.textContent = text;
  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("data-toast-close", "");
  close.setAttribute("aria-label", "Close");
  close.textContent = "×";
  note.append(words, close);
  box.append(note);
  wireToast(note);
}

// A form's Save button sleeps until something in the form changes.
function setUpWatchChanges() {
  document.querySelectorAll("form[data-watch-changes]").forEach((form) => {
    const button = form.querySelector('button[type="submit"]:not([name])');
    if (!button) return;
    button.disabled = true;
    const wake = () => (button.disabled = false);
    form.addEventListener("input", wake);
    form.addEventListener("change", wake);
  });
}

// Group info: find someone in the People list as you type.
function setUpPeopleSearch() {
  const box = document.querySelector("[data-people-search]");
  const list = document.querySelector("[data-people]");
  if (!box || !list) return;
  const none = document.querySelector("[data-people-none]");
  box.addEventListener("input", () => {
    const wanted = box.value.trim().toLowerCase();
    let shown = 0;
    list.querySelectorAll(":scope > .member-row").forEach((row) => {
      const text = row.querySelector(".member-text").textContent.toLowerCase();
      row.hidden = wanted !== "" && !text.includes(wanted);
      if (!row.hidden) shown += 1;
    });
    if (none) none.hidden = shown > 0;
  });
}

// Startup: every part looks for its own elements and does nothing if they aren't on the page.
const setUps = [setUpToasts, setUpWatchChanges, setUpPeopleSearch, setUpInstall, setUpTheme, setUpAccent, setUpPopovers,
  setUpConfirm, setUpAutoSubmit, setUpCopy];
document.addEventListener("DOMContentLoaded", () => setUps.forEach((setUp) => setUp()));

// The chat pages, live: sending over the WebSocket, new messages arriving, unread counts,
// and catching up after a reconnect. Without this file every page still works (forms).
//
// Messages arrive with their HTML already drawn by the server (the same template as the
// page, escaped there), so this file never builds markup from what people typed: it only
// sets textContent for the "Sending..." bubble.
(() => {
  const body = document.body;
  const me = Number(body.dataset.me);
  if (!me || typeof Live === "undefined") return;

  const zone = body.dataset.timeZone || undefined;
  const clockFormat = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit", timeZone: zone });
  const dayKeyFormat = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
  const weekdayFormat = new Intl.DateTimeFormat("en-US", { timeZone: zone, weekday: "long" });
  const dateFormat = new Intl.DateTimeFormat("en-US", { timeZone: zone, month: "short", day: "numeric" });
  const FOLLOW_GAP = 5 * 60 * 1000;

  const conversation = document.querySelector("[data-conversation]");
  const openId = conversation ? Number(conversation.dataset.conversation) : null;
  const list = document.querySelector("[data-chat-list]");
  const messages = document.querySelector("[data-messages]");
  const scroller = document.querySelector("[data-scroll]");
  const totalBadge = document.querySelector("[data-total-unread]");
  const connectionNote = document.querySelector("[data-connection]");
  const baseTitle = document.title.replace(/^\(\d+\) /, "");
  let version = conversation ? Number(conversation.dataset.version) : 0;
  let lastSeq = conversation ? Number(conversation.dataset.lastSeq) : 0;
  let readSeq = conversation ? Number(conversation.dataset.readSeq) : 0;
  let total = totalBadge ? Number(totalBadge.textContent) || 0 : 0;

  const fromHTML = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  };

  const newId = () => {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  };

  // ---------- Times, in the reader's own time zone ----------

  function dayLabel(date) {
    const key = dayKeyFormat.format(date);
    const today = new Date();
    if (key === dayKeyFormat.format(today)) return "Today";
    if (key === dayKeyFormat.format(new Date(today - 864e5))) return "Yesterday";
    if (today - date < 6 * 864e5) return weekdayFormat.format(date);
    return dateFormat.format(date);
  }

  function localize(root) {
    root.querySelectorAll("time[data-clock]").forEach((time) => {
      time.textContent = clockFormat.format(new Date(time.dateTime));
    });
  }

  // ---------- Unread counts: each chat's badge, the menu's total, the tab's title ----------

  const rowOf = (id) => list && list.querySelector(`[data-chat="${id}"]`);

  function setRowUnread(row, count) {
    const badge = row.querySelector("[data-unread]");
    if (!badge) return;
    badge.querySelector("[data-unread-number]").textContent = count;
    badge.hidden = count <= 0;
    row.classList.toggle("has-unread", count > 0);
  }

  const rowUnread = (row) => Number(row.querySelector("[data-unread-number]").textContent) || 0;

  function setTotal(count) {
    total = Math.max(0, count);
    if (totalBadge) {
      totalBadge.textContent = total;
      totalBadge.hidden = total === 0;
    }
    document.title = total > 0 ? `(${total}) ${baseTitle}` : baseTitle;
  }

  // ---------- Reading ----------

  const nearBottom = () => !scroller || Math.abs(scroller.scrollTop) < 120; // column-reverse: 0 is the bottom
  const reading = () => openId !== null && document.visibilityState === "visible" && nearBottom();

  function markRead(seq) {
    if (!openId || seq <= readSeq) return;
    readSeq = seq;
    const row = rowOf(openId);
    if (row) setRowUnread(row, 0);
    Live.request("read", { conversation_id: openId, seq }).catch(() => {});
  }

  // ---------- Messages in the open chat ----------

  function lastMessageEl() {
    const all = messages.querySelectorAll(".msg");
    return all.length ? all[all.length - 1] : null;
  }

  // The server drew the message for everyone: make it yours if it is, join it to the run
  // before it, put a day line before it if a new day started, and write its time.
  function decorate(el, before) {
    if (el.dataset.sender && Number(el.dataset.sender) === me) {
      el.classList.remove("theirs");
      el.classList.add("mine");
    }
    if (before && !el.classList.contains("msg-system") && !before.classList.contains("msg-system")
        && before.dataset.sender === el.dataset.sender
        && new Date(el.dataset.at) - new Date(before.dataset.at) <= FOLLOW_GAP
        && dayKeyFormat.format(new Date(el.dataset.at)) === dayKeyFormat.format(new Date(before.dataset.at))) {
      el.classList.add("follows");
    }
    addTokens(el);
    markMyReactions(el);
    localize(el);
  }

  function append(el) {
    const start = messages.querySelector("[data-chat-start]");
    if (start) start.remove();
    const before = lastMessageEl();
    const at = new Date(el.dataset.at);
    if (!before || dayKeyFormat.format(new Date(before.dataset.at)) !== dayKeyFormat.format(at)) {
      const line = document.createElement("li");
      line.className = "day-break";
      const label = document.createElement("span");
      label.textContent = dayLabel(at);
      line.append(label);
      messages.insertBefore(line, messages.querySelector(".latest-anchor"));
    }
    decorate(el, before);
    el.classList.add("is-new");
    messages.insertBefore(el, messages.querySelector(".latest-anchor"));
    drawSeen();
  }

  function showMessage(m) {
    if (!messages || m.conversation_id !== openId) return;
    version = Math.max(version, m.version);
    lastSeq = Math.max(lastSeq, m.seq);
    const existing = document.getElementById(`m-${m.id}`);
    const fresh = fromHTML(m.html);
    if (existing) {
      // An edit, a delete, a reaction (stage 6): the newer drawing replaces the old one.
      if (existing.classList.contains("follows")) fresh.classList.add("follows");
      decorate(fresh, null);
      existing.replaceWith(fresh);
      drawSeen();
      return;
    }
    const waiting = m.client_id && messages.querySelector(`[data-client-id="${m.client_id}"]`);
    if (waiting) {
      const before = waiting.previousElementSibling && waiting.previousElementSibling.classList.contains("msg")
        ? waiting.previousElementSibling : null;
      decorate(fresh, before);
      waiting.replaceWith(fresh);
      drawSeen();
      return;
    }
    append(fresh);
  }

  // ---------- The chat list ----------

  function updateRow(m) {
    if (!list) return;
    const row = rowOf(m.conversation_id);
    if (!row) {
      resync(); // a chat we don't know yet
      return;
    }
    const mine = m.sender_id === me;
    let prefix = "";
    if (m.kind !== "system") prefix = mine ? "You: " : row.dataset.kind === "group" && m.sender_first ? `${m.sender_first}: ` : "";
    row.querySelector("[data-preview]").textContent = prefix + m.preview;
    const when = row.querySelector("[data-when]");
    when.dateTime = m.created_at;
    when.textContent = clockFormat.format(new Date(m.created_at));
    row.dataset.lastSeq = m.seq;
    list.prepend(row);
  }

  function markOpenRow() {
    if (!list || !openId) return;
    const row = rowOf(openId);
    if (!row) return;
    row.classList.add("is-open");
    row.querySelector("a").setAttribute("aria-current", "page");
  }

  // ---------- Sending ----------

  function pendingBubble(text, clientId) {
    const li = document.createElement("li");
    li.className = "msg mine is-pending";
    li.dataset.clientId = clientId;
    li.dataset.sender = String(me);
    li.dataset.at = new Date().toISOString();
    li.innerHTML = '<span class="msg-avatar"></span><div class="msg-body"><div class="bubble">' +
      '<p class="msg-text"></p><span class="msg-meta">Sending...</span></div></div>';
    li.querySelector(".msg-text").textContent = text; // text, never HTML
    return li;
  }

  function sendMessage(text, clientId, el, replyTo) {
    el.classList.remove("is-failed");
    el.classList.add("is-pending");
    el.querySelector(".msg-meta").textContent = "Sending...";
    const data = { conversation_id: openId, body: text, client_id: clientId };
    if (replyTo) data.reply_to = replyTo;
    Live.request("message.send", data)
      .then((ack) => {
        const m = ack.message;
        readSeq = Math.max(readSeq, m.seq);
        if (document.getElementById(`m-${m.id}`)) {
          el.remove(); // message.new got here first and already put the saved copy in
        } else if (el.isConnected) {
          showMessage(m);
        }
      })
      .catch((error) => {
        el.classList.remove("is-pending");
        el.classList.add("is-failed");
        el.querySelector(".msg-meta").textContent = "Not sent. Tap to try again.";
        if (typeof toast === "function" && error && error.message) toast(error.message, true);
        el.addEventListener("click", () => sendMessage(text, clientId, el, replyTo), { once: true });
      });
  }

  function setUpComposer() {
    const form = document.querySelector("[data-composer]");
    if (!form || !openId) return;
    const area = form.querySelector("[data-composer-text]");
    const fit = () => {
      area.style.height = "auto";
      area.style.height = `${Math.min(area.scrollHeight, 160)}px`;
    };
    area.addEventListener("input", fit);
    area.addEventListener("keydown", (event) => {
      // Enter sends; Shift+Enter is a new line. Not while an input method is composing a word.
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = area.value.trim();
      if (!text) return;
      const clientId = newId();
      const replyTo = replyInput && Number(replyInput.value) ? Number(replyInput.value) : null;
      const el = pendingBubble(text, clientId);
      append(el);
      area.value = "";
      fit();
      setReply(null);
      if (scroller) scroller.scrollTop = 0;
      sendMessage(text, clientId, el, replyTo);
      document.dispatchEvent(new CustomEvent("paperplane:sent"));
    });
  }

  // ---------- Catching up after a reconnect ----------

  let syncing = false;
  function resync() {
    if (syncing) return;
    syncing = true;
    Live.request("sync", { open: openId ? { conversation_id: openId, version } : null }, { timeout: 15000 })
      .then((answer) => {
        if (answer.gone) {
          location.href = "/chats";
          return;
        }
        if (answer.reload) {
          location.reload();
          return;
        }
        if (list) {
          list.innerHTML = answer.list_html;
          localize(list);
          markOpenRow();
          const empty = document.querySelector("[data-chat-list-empty]");
          if (empty && list.children.length) empty.remove();
        }
        setTotal(answer.total_unread);
        (answer.changes || []).forEach(showMessage);
        if (typeof answer.version === "number") version = Math.max(version, answer.version);
        if (reading()) markRead(lastSeq);
        document.dispatchEvent(new CustomEvent("paperplane:synced", { detail: answer }));
      })
      .catch(() => {})
      .finally(() => {
        syncing = false;
      });
  }

  // ---------- What the server tells us ----------

  Live.on("message.new", (m) => {
    const mine = m.sender_id === me;
    updateRow(m);
    if (m.conversation_id === openId) showMessage(m);
    if (mine) return;
    if (m.conversation_id === openId && reading()) {
      markRead(m.seq);
    } else {
      const row = rowOf(m.conversation_id);
      if (row && !row.dataset.muted) setRowUnread(row, rowUnread(row) + 1);
      if (!row || !row.dataset.muted) setTotal(total + 1);
    }
    document.dispatchEvent(new CustomEvent("paperplane:message", { detail: m }));
  });

  Live.on("message.updated", (m) => {
    showMessage(m);
    const row = rowOf(m.conversation_id);
    if (row && Number(row.dataset.lastSeq) === m.seq) {
      const mine = m.sender_id === me;
      const prefix = mine ? "You: " : row.dataset.kind === "group" && m.sender_first ? `${m.sender_first}: ` : "";
      row.querySelector("[data-preview]").textContent = prefix + m.preview;
    }
  });

  Live.on("read", (data) => {
    if (data.user_id !== me) {
      if (data.conversation_id === openId) {
        const reader = readers[String(data.user_id)];
        if (reader) reader.seq = Math.max(reader.seq, data.seq);
        drawSeen();
      }
      return;
    }
    const row = rowOf(data.conversation_id);
    if (row) setRowUnread(row, Math.max(0, Number(row.dataset.lastSeq || 0) - data.seq));
    if (data.conversation_id === openId) readSeq = Math.max(readSeq, data.seq);
  });

  Live.on("unread.total", (data) => setTotal(data.total));

  Live.on("conversation.new", (data) => {
    if (!list || rowOf(data.conversation_id)) return;
    const row = fromHTML(data.html);
    localize(row);
    list.prepend(row);
    const empty = document.querySelector("[data-chat-list-empty]");
    if (empty) empty.remove();
  });

  Live.on("reconnected", resync);

  Live.on("status", (status) => {
    if (!connectionNote) return;
    const words = {
      reconnecting: "Reconnecting... Messages you send now will go out when it's back.",
      "too-many": "You have too many tabs open. Close one, then reload this page.",
    };
    connectionNote.textContent = words[status] || "";
    connectionNote.hidden = !words[status];
  });

  document.addEventListener("visibilitychange", () => {
    if (reading()) markRead(lastSeq);
  });

  // ---------- Message tools: reply, edit, delete, react, "Seen by" ----------

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.content : "";

  // Live messages come without a CSRF token in their forms (it would be the sender's): add ours.
  function addTokens(root) {
    root.querySelectorAll('form[method="post"]').forEach((form) => {
      if (form.querySelector('input[name="csrf_token"]')) return;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = csrfToken;
      form.prepend(input);
    });
  }

  function markMyReactions(root) {
    root.querySelectorAll(".reaction[data-users]").forEach((chip) => {
      chip.classList.toggle("is-mine", chip.dataset.users.split(",").map(Number).includes(me));
    });
  }

  const readers = conversation && conversation.dataset.readers ? JSON.parse(conversation.dataset.readers) : {};
  const seenLine = document.querySelector("[data-seen-line]");

  // Under my last message: "Seen" in a DM, "Seen by Ana and Ben" or "Seen by everyone" in a group.
  function drawSeen() {
    if (!seenLine || !messages) return;
    const last = lastMessageEl();
    let text = "";
    if (last && !last.classList.contains("msg-system") && !last.classList.contains("is-pending")
        && Number(last.dataset.sender) === me) {
      const seq = Number(last.dataset.seq);
      const others = Object.entries(readers).filter(([id]) => Number(id) !== me);
      const seen = others.filter(([, reader]) => reader.seq >= seq).map(([, reader]) => reader.name);
      if (seen.length) {
        if (conversation.dataset.kind === "dm") text = "Seen";
        else if (seen.length === others.length && others.length > 1) text = "Seen by everyone";
        else if (seen.length === 1) text = `Seen by ${seen[0]}`;
        else text = `Seen by ${seen.slice(0, -1).join(", ")} and ${seen[seen.length - 1]}`;
      }
    }
    seenLine.textContent = text;
    seenLine.hidden = !text;
    messages.insertBefore(seenLine, messages.querySelector(".latest-anchor"));
  }

  const replyBanner = document.querySelector("[data-reply-banner]");
  const replyInput = document.querySelector("[data-reply-to]");

  function setReply(id, name, snippet) {
    if (!replyBanner || !replyInput) return;
    replyInput.value = id || "";
    if (id) {
      replyBanner.querySelector("[data-reply-name]").textContent = name || "";
      replyBanner.querySelector("[data-reply-snippet]").textContent = snippet || "";
    }
    replyBanner.hidden = !id;
  }

  const showError = (error) => {
    if (typeof toast === "function") toast((error && error.message) || "That didn't work. Please try again.", true);
  };

  const closeMenus = (root) => root.querySelectorAll("details[open]").forEach((menu) => (menu.open = false));

  function startEdit(li) {
    const textEl = li.querySelector("[data-text]");
    if (!textEl || li.querySelector(".edit-box")) return;
    const box = document.createElement("form");
    box.className = "edit-box";
    const field = document.createElement("textarea");
    field.value = textEl.textContent;
    field.maxLength = 4000;
    field.setAttribute("aria-label", "Edit your message");
    const actions = document.createElement("div");
    actions.className = "form-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button button-quiet button-small";
    cancel.textContent = "Cancel";
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "button button-small";
    save.textContent = "Save";
    actions.append(cancel, save);
    box.append(field, actions);
    textEl.hidden = true;
    textEl.after(box);
    field.focus();
    const stop = () => {
      box.remove();
      textEl.hidden = false;
    };
    cancel.addEventListener("click", stop);
    field.addEventListener("keydown", (event) => {
      if (event.key === "Escape") stop();
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        box.requestSubmit();
      }
    });
    box.addEventListener("submit", (event) => {
      event.preventDefault();
      const body = field.value.trim();
      if (!body) return;
      save.disabled = true;
      Live.request("message.edit", { message_id: Number(li.dataset.id), body }, { timeout: 15000 })
        .catch((error) => {
          save.disabled = false;
          showError(error);
        }); // message.updated draws the edited message for everyone, this tab too
    });
  }

  function setUpTools() {
    if (!messages) return;
    // Forms in a message (reactions, delete): over the socket instead of a page load. The
    // delete form asks first (app.js); its first submit is stopped there, the approved one comes here.
    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (event.defaultPrevented || !form.dataset || !form.dataset.tool || !messages.contains(form)) return;
      event.preventDefault();
      const li = form.closest(".msg");
      closeMenus(li);
      const id = Number(li.dataset.id);
      if (form.dataset.tool === "react") {
        const emoji = form.querySelector('input[name="emoji"]').value;
        Live.request("message.react", { message_id: id, emoji }, { timeout: 15000 }).catch(showError);
      } else if (form.dataset.tool === "delete") {
        Live.request("message.delete", { message_id: id }, { timeout: 15000 }).catch(showError);
      }
    });
    messages.addEventListener("click", (event) => {
      const tool = event.target.closest("a[data-tool]");
      if (tool) {
        event.preventDefault();
        const li = tool.closest(".msg");
        closeMenus(li);
        if (tool.dataset.tool === "reply") {
          setReply(li.dataset.id, tool.dataset.replyName, tool.dataset.replySnippet);
          const area = document.querySelector("[data-composer-text]");
          if (area) area.focus();
        } else if (tool.dataset.tool === "edit") {
          startEdit(li);
        }
        return;
      }
      const quote = event.target.closest("[data-jump]");
      if (quote) {
        const target = document.getElementById(`m-${quote.dataset.jump}`);
        if (!target) return; // an older message: the link loads it
        event.preventDefault();
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.classList.add("is-flash");
        setTimeout(() => target.classList.remove("is-flash"), 1600);
      }
    });
    const cancel = document.querySelector("[data-reply-cancel]");
    if (cancel) {
      cancel.addEventListener("click", (event) => {
        event.preventDefault();
        setReply(null);
      });
    }
  }

  // ---------- Presence: the dots, and the line under a DM's name ----------

  const statuses = new Map();

  function agoWords(iso) {
    const seconds = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) {
      const hours = Math.floor(seconds / 3600);
      return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    }
    const label = dayLabel(new Date(iso));
    return label === "Yesterday" ? "yesterday" : `on ${label}`;
  }

  function showPresence(userId, status, lastSeen) {
    statuses.set(userId, status);
    document.querySelectorAll(`[data-presence-dot="${userId}"]`).forEach((dot) => {
      dot.className = `dot dot-${status}`;
    });
    document.querySelectorAll(`[data-status-line][data-user="${userId}"]`).forEach((line) => {
      if (lastSeen) line.dataset.lastSeen = lastSeen;
      else if (status === "offline" && lastSeen === null) delete line.dataset.lastSeen;
      if (status === "online") line.textContent = "Online";
      else if (status === "away") line.textContent = "Away";
      else line.textContent = line.dataset.lastSeen ? `Last seen ${agoWords(line.dataset.lastSeen)}` : `@${line.dataset.handle}`;
    });
  }

  Live.on("presence", (data) => showPresence(data.user_id, data.status, data.last_seen));
  Live.on("presence.snapshot", (data) => {
    Object.entries(data.statuses || {}).forEach(([userId, status]) => showPresence(Number(userId), status, undefined));
  });

  // My tab hidden or shown: after 5 minutes with every tab hidden, friends see "Away".
  const tellVisibility = () => Live.tell("presence", { visible: document.visibilityState === "visible" });
  document.addEventListener("visibilitychange", tellVisibility);
  Live.on("status", (status) => {
    if (status === "online" && document.visibilityState !== "visible") tellVisibility();
  });

  // "Last seen 3 min ago" keeps up with the clock.
  setInterval(() => {
    document.querySelectorAll("[data-status-line][data-last-seen]").forEach((line) => {
      const status = statuses.get(Number(line.dataset.user));
      if (!status || status === "offline") line.textContent = `Last seen ${agoWords(line.dataset.lastSeen)}`;
    });
  }, 30000);

  // ---------- Typing ----------

  const typingLine = document.querySelector("[data-typing]");
  const typists = new Map(); // user id -> { name, timer }

  function drawTyping() {
    if (!typingLine) return;
    const names = Array.from(typists.values(), (typist) => typist.name);
    if (names.length === 0) typingLine.textContent = "";
    else if (names.length === 1) typingLine.textContent = `${names[0]} is typing...`;
    else if (names.length === 2) typingLine.textContent = `${names[0]} and ${names[1]} are typing...`;
    else typingLine.textContent = "Several people are typing...";
  }

  function stopTyping(userId) {
    const typist = typists.get(userId);
    if (!typist) return;
    clearTimeout(typist.timer);
    typists.delete(userId);
    drawTyping();
  }

  Live.on("typing", (data) => {
    if (data.conversation_id !== openId || data.user_id === me) return;
    stopTyping(data.user_id);
    typists.set(data.user_id, { name: data.name, timer: setTimeout(() => stopTyping(data.user_id), 5000) });
    drawTyping();
  });

  // Their message arrived: they've stopped typing.
  document.addEventListener("paperplane:message", (event) => stopTyping(event.detail.sender_id));

  function setUpTypingSignal() {
    const area = document.querySelector("[data-composer-text]");
    if (!area || !openId) return;
    let lastSignal = 0;
    area.addEventListener("input", () => {
      const now = Date.now();
      if (area.value.trim() && now - lastSignal > 3000) {
        lastSignal = now;
        Live.tell("typing", { conversation_id: openId });
      }
    });
    document.addEventListener("paperplane:sent", () => (lastSignal = 0));
  }

  // ---------- Start ----------

  document.addEventListener("DOMContentLoaded", () => {
    if (messages) localize(messages);
    setUpComposer();
    setUpTypingSignal();
    setUpTools();
    if (messages) markMyReactions(messages);
    if (scroller) {
      scroller.addEventListener("scroll", () => {
        if (reading()) markRead(lastSeq);
      }, { passive: true });
    }
    setTotal(total);
    Live.start();
  });

  window.PaperPlane = { me, openId, rowOf, setRowUnread, setTotal, markRead, resync };
})();
